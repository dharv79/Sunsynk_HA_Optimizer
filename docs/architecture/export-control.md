# Export control

16:00–19:00 export-disable: watt vs cost trigger, shadow mode, weekly tally.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Cost-aware export-disable threshold (v1.0.11 Part 3, shadow mode)

`async_run_flux2_check` computes two independent triggers for the 16:00–19:00 export-disable decision: `watt_trigger` (the original `grid_pac > CONF_EXPORT_DISABLE_THRESHOLD` check) and `cost_trigger` (`(grid_pac_kw × peak import price) > CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR`, using the `peak` band from `_tariff_prices()` (Octopus rates entity first, then the user's own configured `charges` via `flux_helpers.tariff_prices_pence`) — a pure helper, unit-tested without HA). `cost_trigger` is `None` outside the window or when `charges` has no matching import row for it — never coerced to a false 0p price. `CONF_COST_AWARE_EXPORT_SHADOW_MODE` (default `True`) gates which trigger actually drives the real decision: shadow mode on → `watt_trigger` always wins (zero behaviour change from pre-Part-3); shadow mode off → `cost_trigger` wins, falling back to `watt_trigger` if `cost_trigger` is `None`. Every check where the two triggers disagree is tallied on `self._shadow_export_stats` (unpersisted, same transient-state pattern as `_peak_window_start`) via `_tally_shadow_export_divergence` — `estimated_gbp_delta` only accumulates for the "cost says pause, watt didn't" direction, since the reverse carries no cost risk (export was already disabled). `_async_send_weekly_cost_summary` (Part 2) reads and resets this tally each Sunday, folding it into the weekly digest as `cost_aware_export_shadow_tally` so divergence is visible without a dedicated sensor. Default cost threshold (`DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR = 58.32`) is back-computed from the default Watt threshold (1.5 kW) × the default 16:00–19:00 import price (38.88 p/kWh), so a fresh upgrade stays behaviour-neutral even before the user touches the new config fields.


## Evening reserve to 02:00 (09/10/2026, phase 16)

**Problem.** A sell-down could leave too little in the battery to run the house from 19:00 to the 02:00 off-peak start, so the evening was bought at the day rate.

**Design.**

- `planning.evening_reserve_soc(load_kw, capacity)` is `min(100, ceil(20 + load × 7 h / capacity × 100))`. The load is `last_import_plan.avg_consumption_kw` (phase 12 learned or config), falling back to the config rate.
- `planning.trim_target_soc(soc, reserve)` returns `max(82, reserve)`, or `None` when that is not below the SOC.
- Both the daytime trim (`trim_to_82`) and the full-charge-day trim (`full_day_trim_to_82`) push that target, or skip when it is `None`. The action names are kept for history. The Flux 2 sensor reads the real target from the payload.
- The notification says "to keep the evening reserve" when the reserve set the target. `last_flux2_action` carries `evening_reserve_soc`.
- Phase 17 peak export will use the same floor.
- Do-not-break 7 (reload one-shot and free-event slots) is untouched.

**Logged.** The 22:00 `day_kpis` line carries `evening_reserve_soc`. A new KPI, `grid_import_evening_kwh`, covers 19:00–22:00; the meters reset at midnight, so 22:00–02:00 is not visible.

**Verification.** `tests/test_planning.py` covers the reserve maths, the cap and the `max` floor, including skipping when nothing is left to trim. `tests/test_kpis.py` covers the evening band. In HA, a trim notification says "to keep the evening reserve" only when the reserve is above 82%.


## Peak surplus export, shadow (09/10/2026, phase 17)

**Goal.** Sell what the evening will not need at the Flux peak export rate, instead of only trimming to a fixed 82%.

**Design.** A 16:00 listener (`_async_peak_export`, run through `_guarded`) calls `planning.peak_export_plan(soc, reserve, capacity, export_p, offpeak_p)`.

- The target is the evening reserve (phase 16) plus a 5% margin, capped at 100. Since b25 it also adds the expected 16:00–19:00 use (`peak_need_soc`, see below).
- The surplus is the SOC above the target, in kWh.
- `planning.arbitrage_worth_it` says to sell only if the peak export price beats the off-peak price ÷ 0.85 round-trip efficiency, plus battery wear. The wear term comes from the phase 24 options (`None` when blank).
- The decision is one of:
  - `export`;
  - `no_surplus` (< 0.5 kWh);
  - `not_worth_it`;
  - `no_price`, because a missing price is never treated as 0p;
  - `export_disabled`, when the watt-trigger pause is already active at 16:00. The export-disable always wins: if it fires later it pushes its 100% hold over the export window.
- Prices come from `flux_helpers.kpi_prices_pence`.

**Shadow and live.** The new option `peak_export_live` defaults off.

- In shadow the decision is only logged.
- Live (and not in monitor mode, do-not-break 16) pushes Flux 2 (index 1, do-not-break 3) 16:00–19:00 to the target as action `peak_export`, via `_async_post_with_status`. It notifies "🔋 Sunsynk: selling surplus at peak rate" or "⚠️ Sunsynk: peak export NOT applied", gated on the bool.
- While a successful live export holds Flux 2, the daytime and full-charge-day trims are skipped so they don't replace the window (`_peak_export_holds_flux2`, reading the persisted `state.peak_export`).
- The export is skipped entirely while a free event holds the slots (do-not-break 7).

**Logged.** A `peak_export` record (deduped per day) holds `soc`, `evening_reserve_soc`, `decision`, `target_soc`, `export_kwh`, `export_gbp`, `gain_gbp` (export income minus replacement cost), `live` and `api_ok`.

- It appears in the 22:00 bundle.
- `_pair_records` carries `peak_export_kwh` / `peak_export_gain_gbp` / `peak_export_live` for export days only.
- The Sunday digest adds `week_peak_export_kwh`, `week_peak_export_gain_gbp` and `peak_export_live`.

**Verification.** `tests/test_kpis.py` covers:

- the worth-it margin, the wear term, and missing prices;
- that a surplus sets the target and kWh/£;
- no surplus, not worth it, no price, and the 100 cap;
- record dedup and export-only pairing.

The free-event, monitor-mode and export-disable gates are early returns in the HA-only listener. In HA, the 22:00 bundle shows a `peak_export` line with `live: false`.

**Persisted decision (15/10/2026).** The 16:00 record is stored in `OptimizerState.peak_export` (via `update_state`) instead of an instance attribute. Root cause: an HA restart between 16:00 and 22:00 (e.g. an update) dropped the day's `peak_export` line from the 22:00 debug bundle and released a live export's hold on Flux 2. `planning.record_for_date` returns it only for today. Test: `test_record_for_date`.

**Skip reasons logged (16/10/2026).** Every early exit from the 16:00 run now writes a `peak_export` record whose `decision` is `skipped_slots_held` or `skipped_no_soc` (`skipped_saving_session` existed until saving sessions were removed the same day) (`planning.peak_export_skip_reason`). Root cause: on 15/10 a manual saving session at 18:00 made the run skip by design, but it wrote nothing, so the 22:00 bundle had no line and looked like a failure. Pairing only reads `decision == "export"`, and the Flux 2 hold needs `api_ok`, so skip records change no behaviour. Test: `test_peak_export_skip_reason`.

## Tariff rates from the Octopus integration (09/10/2026, phase 18)

**Design.** Two optional config fields, `octopus_import_rates_entity` and `octopus_export_rates_entity`, take either of these Octopus Energy integration entities:

- the current-rate sensor (its `start`/`end` attributes, with the state as the rate);
- the `…_current_day_rates` event (its `rates` list; the legacy `all_rates` also works).

Rates are £/kWh inc VAT.

`flux_helpers.octopus_rate_pence_per_kwh` returns the time-weighted pence for an HH:MM window. It uses the decision date's window, or the latest date the entity covers. `tariff_prices_pence` overlays these on `kpi_prices_pence(charges)` per `TARIFF_BANDS` band, giving `(prices, sources)` where each source is `"octopus"`, `"charges"` or `None`. `optimizer._tariff_prices` feeds three callers:

- the cost trigger (peak);
- the 22:00 KPIs;
- the phase 17 peak export.

A blank or unusable entity falls back to `charges` per band. A price missing everywhere stays `None`, never 0p (do-not-break 14).

**Logged.** `price_source` (`octopus` / `charges` / `mixed` / `None`) is recorded in three places:

- on `last_flux2_action` during the peak window;
- on `day_kpis`;
- on `peak_export`.

Shadow mode is unchanged: the watt trigger still drives the decision.

**Verification.** `tests/test_octopus_rates.py` covers:

- window pricing from the rates list, including time weighting;
- date preference;
- the current-rate block;
- unusable entities → `None`;
- sensor rates overriding `charges`, with per-band fallback and source labels;
- missing everywhere → `None`.

In HA, set the import entity, then check that the 22:00 `day_kpis` line shows `price_source: "octopus"` (or `"mixed"` if only import is set).

## Peak cover (13/10/2026, phase 30)

**Why.** On 12/10 (dull day) the overnight charge stopped at ~83%, the battery hit its 25% low-battery limit by ~14:30 and 2.7 kWh was bought at the 38.88p peak rate during a grid charge that ran into 16:00–19:00. Other days peak imports were 0.1–0.4 kWh, so this is an occasional-shortfall guard, worth roughly £0.2 on a bad day.

**Logic (`planning.peak_cover_plan`).** Every 30 minutes 12:00–16:00 (`async_run_peak_cover`, from the periodic tick):
- Projected SOC at 16:00 = SOC + (forecast solar to 16:00 − load × hours) / capacity. Solar is the raw (uncorrected) forecast — the pessimistic choice, like the low-solar decisions (do-not-break 8) — from the hourly sensor or the synthetic profile; no forecast means no solar assumed. Load is the observed average since 05:00 from the meter snapshots once 2 h exist, else tonight's plan load. A battery that would run flat is held at the 25% floor (`PEAK_COVER_FLOOR_SOC`, the user's Low Batt setting).
- Required SOC = floor + 75th-percentile recent `peak_load_kwh` (≥3 days, else 3 h × load) × 1.1 margin ÷ 0.92 discharge efficiency (b25, `peak_need_soc`).
- `top_up` when the shortfall is ≥3 points and the day price beats the peak price after charge losses (`peak_cover_worth_it`); a missing price → `no_price`, never a decision to charge.
- The push waits until the charge would need to start (+45 min lead, `push_now`) so later solar can still help.

**Live (`peak_cover_live`, default off).** A Flux 1 slot now→16:00 with `targetSoc` = the required SOC. The inverter imports only while below the target, so it holds the battery there at the day rate rather than charging in one go. One push a day; at 16:00 `_async_peak_cover_end` (in `async_run_peak_export`) restores the normal plan (`_async_restore_normal_plan`). Never in monitor mode; skipped while a free event holds the slots.

**Shadow / logging.** One `peak_cover` record per day (dedup): the first actionable top-up, else the last check before 16:00, with the projection, required SOC, load/solar inputs, prices, `live` and `api_ok`; it rides the 22:00 debug bundle. Transient state `_peak_cover` is unpersisted by design: after a restart it re-evaluates.

**Known limits.** The load estimate before 07:00 falls back to the plan load, which is overnight-only and low. A Flux 1 slot replaces the stored import slot until the next push: the 16:00 restore re-pushes the last plan, and the 01:55 plan re-pushes every night.

## Peak cover discharge losses and peak export keeps the peak load (20/10/2026, b25)

**Root cause.** On 20/10 (0.6 kWh of solar) the first live peak cover topped up to 62% at 14:02 and hit it at 16:00. But 0.5 kWh was still bought at the peak rate: the target held 3.3 kWh × 1.1 = 3.63 kWh above the floor, and after ~8% discharge losses that delivers ~3.35 kWh, against 3.4 kWh used. The same day the shadow peak export would have sold 0.8 kWh down to 54% (reserve 49 + 5). That reserve covers only 19:00–02:00, so the 16:00–19:00 load would have been bought back at the peak rate.

**Fix.**
- `planning.peak_need_soc(peak_load_kwh, capacity)` gives the SOC points the peak takes out of the battery: load × 1.1 margin ÷ `DISCHARGE_EFFICIENCY` (0.92) ÷ capacity. `peak_cover_plan`'s required SOC is the floor + this; 20/10 would have been 66% rather than 62%.
- `peak_export_plan(..., peak_need_soc=)` puts the target at reserve + peak need + 5%, rounded up and capped at 100. `async_run_peak_export` sizes the peak need like peak cover does: the 75th-percentile recent `peak_load_kwh`, falling back to 3 h × plan load. The `peak_export` record adds `peak_load_kwh`, `peak_load_source` and `peak_need_soc`.
- Effect: at current loads (a reserve of about 49% plus about 40 points of peak need), peak export only sells above about 94%, so the shadow gain falls to near zero. That is the honest number, because the old figure sold energy the peak then bought back.

**Open before peak export goes live.** The live export holds Flux 2 at the target from 16:00 to 19:00. If the inverter then holds the battery at the target, rather than letting it serve the house once the target is reached, the peak load is still imported. Check the inverter's behaviour on the first live day.

**Verification.** `tests/test_peak_cover.py::test_peak_need_allows_for_discharge_losses` and the updated required-SOC test; `tests/test_kpis.py::test_peak_export_keeps_the_peak_load`. In HA, the 22:00 `peak_export` line shows `peak_need_soc`, and its `target_soc` is about 90+.

## Peak cover sizing: wear and a weekday median (01/11/2026, phase 60)

**Root cause.** On 31/10 the top-up assumed 4.0 kWh of peak load but 2.4 kWh was used. `peak_load_estimate_kwh` took the p75 and `peak_need_soc` added a 1.1 margin on top. `peak_cover_worth_it` also ignored battery wear, so a top-up with only 8.7p/kWh of day-vs-peak margin looked profitable when 6-9p of wear erased it.

**Design.** `peak_cover_worth_it(..., wear_pence)` requires `peak > day/eff + wear` (unknown wear counts as none). `peak_load_estimate_split` uses the median of weekday samples with margin 1.0 once there are 7; weekends and thinner history keep the all-days p75 x 1.1. `peak_cover` records gain `peak_margin`, `wear_cost_p` and `gain_after_wear_gbp`. The peak-export sizing is unchanged.

**Verification.** `tests/test_peak_cover.py`.

## Peak cover hold (01/11/2026, phase 59)

**Root cause.** On 31/10 the SOC fell from 95 to 40 by 13:45 and peak cover then bought 4.71 kWh at the day rate to get back to 73. The import price is the same either way, but recharging pays the 0.92 charge loss and an extra cycle that holding the battery earlier would have avoided.

**Design.** `peak_cover_plan` returns `hold` when SOC is already at or above the requirement but will fall short by 16:00. It pushes the same Flux 1 now->16:00 slot at `required_soc`. Checks run from 09:00 for holds only; top-ups start at 12:00. Records gain `gain_vs_topup_gbp`. Behind `peak_cover_live`; needs a real-inverter check that a target below the current SOC stops discharge.

**Verification.** `tests/test_peak_cover.py`.

## Peak export re-check (01/11/2026, phase 66)

**Root cause.** The 16:00 peak export sized the peak load from history and never looked again. On 31/10 it assumed 4.0 kWh against 2.4 kWh used, leaving surplus unsold.

**Design.** At 17:00 and 18:00 `peak_export_recheck` projects the remaining load from the observed rate since the 16:00 snapshot and re-runs `peak_export_plan`. Pushes (live only) need a target move of at least 0.5 kWh; a raise needs the 16:00 plan to have been pushed. Logged as `peak_export_recheck`.

**Verification.** `tests/test_peak_export_recheck.py`.

## Peak cover re-checks after a restart (23/10/2026, b26)

**Symptom:** on 23/10 HA restarted during the afternoon; the battery sat at the 25% floor from about 14:00 and the 16:00-19:00 peak ran from the grid with no top-up.

**Root cause:** the 60 s startup one-shot (`_async_initial_refresh`) re-pushes the import plan, whose Flux 1 slot is the overnight 02:00-05:00 window, so a restart after a top-up silently cancels it; `_peak_cover` is unpersisted, so the integration also forgets the push. The next peak cover check then waits for the 30-minute interval, which restarts from the reload: a restart after about 15:30 gets no check before 16:00, and an earlier one loses up to 30 minutes of the charge lead.

**Fix:** `_async_initial_refresh` runs `async_run_peak_cover` straight after the startup plan. It is a no-op outside 12:00-16:00 and while an event holds the slots, and re-pushes the top-up (if one is due) over the startup plan's Flux 1.

**Verification:** `tests/test_code_integrity.py::test_startup_plan_is_followed_by_peak_cover` (AST: the peak cover call follows the startup plan in `_async_initial_refresh`); fails without the fix.

## Peak cover decision survives a restart in the nightly report (23/10/2026, b27)

**Symptom:** the 23/10 22:00 debug batch had no `peak_cover` record although the integration ran all afternoon (no `startup` plan that day), so why no top-up happened could not be read from Slack.

**Root cause:** the 22:00 report took the decision from `_peak_cover["record"]`, which is unpersisted; HA restarted during 16:00-19:00 and the record was lost (it is still in the monthly JSONL via `async_log_peak_cover`).

**Fix:** when the in-memory record is missing or for another date, the report reads the day's last `peak_cover` record from the log (`DataLogger.async_load_peak_cover` → `data_logger.latest_record`, `recorded_at` dropped so the line matches the in-memory shape).

**Verification:** `tests/test_peak_cover.py::test_latest_peak_cover_record_from_the_log`.

## Battery wear cost (phase 24, 13/10/2026)

- Options `battery_cost_gbp` and `battery_lifetime_kwh` (both blank by default). `planning.wear_cost_pence_per_kwh` = cost ÷ lifetime throughput × 100; blank or non-positive → `None` (no wear term, behaviour as before — never 0p).
- `optimizer._wear_pence()` feeds `peak_export_plan` (16:00 peak export). Wear is added to the replacement cost in `arbitrage_worth_it`.
- Logged as `wear_cost_p` on the `peak_export` and `day_kpis` records. Round-trip efficiency stays the 0.85 constant (no phase 14 KPI measures it yet).
- Tests: `tests/test_kpis.py` (phase 24 block).

## Trim time guard (14/10/2026)

**Root cause.** The daytime trim (`soc > 85` → sell to 82%) had no time-of-day check. On a non-full-charge night whose plan targets above 85%, the SOC-change listener fired `async_run_flux2_check` as the charge crossed 85%. The trim then pushed a Flux 2 sell slot to 82% and, because a Flux 2-only push rebuilds Flux 1 from the configured baseline, replaced tonight's import slot. This is the likely cause of the 12/10 charge stopping at ~83%. The same check would also sell a winter 100% charge back at the day export rate soon after 05:00.

**Fix (`planning.trim_target_soc`, via `optimizer._trim_target`).**
- No trim (daytime or full-charge day) from 19:00 to 05:00.
- 05:00–16:00: the floor is `max(82, evening reserve, today's nightly plan target_soc)`, so energy bought at the cheap rate to bridge to solar is never sold at the day export rate. Solar excess above the plan target is still trimmed.
- 16:00–19:00: unchanged (`max(82, reserve)`), since that sells at the peak rate.
- Summer behaviour is unchanged whenever the plan target is ≤82%.

**Verification.** `tests/test_planning.py::test_trim_never_at_night_or_below_tonights_charge`. In HA: on a night planned above 85%, `last_flux2_action` stays `none`/`no_trigger` through the charge, and the 06:00 SOC reaches the plan target.

## Flux slot ownership (14/10/2026)

Every push rebuilds both Flux slots: the slot a caller doesn't override reverts to the configured baseline (`apply_flux_override`). So any two writers can undo each other. Precedence, highest first:

| Writer | Slot | Guarded by |
|---|---|---|
| Free event (holding) | 1 and 2 | owns both; everything else checks `_slots_held()` |
| Export pause (watt/cost trigger, 16–19) | 2 → 100% | wins over peak export; restore keeps it (`restore_flux2_slot`) |
| Live peak export (16:00) | 2 | skipped while slots held or export paused |
| Trims (daytime, full-charge day) | 2 | 05:00–19:00 only; floor ≥ tonight's plan target before 16:00; skip during live peak export, export pause or held slots |
| Live peak cover (12–16) | 1 | skipped while slots held; restored at 16:00 |
| 01:55 plan / watchdog retry | 1 and 2 | skipped while slots held; reload one-shot never 16:00–19:00 |

**Fixed here.**
- **Event end vs export pause:** a free event or saving session ending 16–19 restored the default Flux 2 slot (85%) while state still said paused, so the pause was lost until the trigger cleared and re-fired.
- **Full-charge-day trim:** its 1-hour timer could fire into a free event or saving session (replacing its slots), or into an export pause (selling down, then the pause re-pushed: flip-flop).
- **Reload 16–19:** the one-shot plan undid a live peak export (it was only skipped while paused).

**Verification.** `tests/test_planning.py` (`restore_flux2_slot`).

## Inverter drift check (14/10/2026)

Phase 31. `_async_post_with_status` stores the pushed `fluxProducts` in `state.last_pushed_flux`. Every successful income POST or settings write re-arms a check 10 minutes later, and a 2-hourly interval runs the check too. `async_run_drift_check` (skipped in monitor mode) reads back:
- **Flux slots:** `api.async_read_flux_products` (`GET /api/v1/plant/{plant_id}/income`). This endpoint is undocumented. If the response carries no `fluxProducts`, the Flux check switches itself off until restart (`_flux_read_supported = False`, one warning). The 2-hourly loop keeps running for the grid-current check.
- **Grid-charge current:** the `sdBatteryCurrent` setting, compared only while the integration owns it (`planning.expected_grid_current_a`: gentle value while applied, restored value on the restore day).

`planning.flux_drift` compares start/end as minutes and targetSoc as a number. It skips any field missing or unparseable on either side. `grid_current_drift` uses a 1 A tolerance. Each new drift signature (stored in `state.inverter_drift`) sends a notification, "⚠️ Sunsynk: inverter settings differ from plan", and logs an `inverter_drift` record. A repeat of the same drift is silent, and a clean read clears the episode. Nothing is re-pushed. Tests: `test_flux_drift_*`, `test_expected_grid_current`, `test_grid_current_drift`.

**Live check needed:** confirm the plant income GET returns `data.fluxProducts`. If the log shows "Plant income read returned no fluxProducts", the Flux half goes On hold.

## Evening reserve from the learned evening load, shadow (04/11/2026, phase 55)

`evening_reserve_soc` used the overnight plan rate, but the 16:00-22:00 load is higher in winter. `planning.learned_evening_load_kw` takes the median of (22:00 - 16:00 `load_kwh`) / 6 h over 28 days from the meter snapshots, needing 7 home days (a missing or backwards meter skips the day, away days are skipped, weekday/weekend split when 7 matching days exist). The 01:55 plan refreshes it (`_refresh_evening_load`).

`planning.evening_reserve_choice` clamps it 0.5x-2x the plan rate (like phase 12) and returns (reserve in force, learned reserve). The learned reserve only drives trims, peak export and the peak-cover floor when `evening_load_live` is on (default off). Either way `evening_load_kw_learned` and `evening_reserve_soc_learned` are logged on the 22:00 `day_kpis` record, and `evening_reserve_soc_learned` on the trim action. Compare against `grid_import_evening_kwh` before going live. Unpersisted until the next 01:55 plan after a restart. Tests: `tests/test_evening_load.py`.

## Intraday solar scale, shadow (04/11/2026, phase 56)

`planning.intraday_forecast_scale` compares `pv_etoday` with the hourly forecast summed to now. Peak cover logs the scale, the scaled solar to 16:00 and the decision on min(raw, scaled) beside the unchanged live decision; the spare-solar estimate uses the scale (advisory). Low-solar protection never uses a scale above 1.

## Peak cover after a restart: retry while SOC is unreadable (05/11/2026)

**Root cause.** On 04/11 the 09:16 peak-cover hold at 66 % was lost: every restart re-runs the startup plan, which resets Flux 1 to the overnight window and wipes the hold, and the immediate peak-cover re-check returned early because the grid-outage flag was (falsely) set on the b36 restart and, on later restarts, the battery SOC sensor had not polled yet. SOC fell to 39 % by 16:00 and 1.6 kWh came from the grid in the peak.

**Fix.** `_async_initial_refresh` now calls `_async_peak_cover_after_startup`, which runs peak cover and, while `planning.peak_cover_startup_retry_due` holds (inside 09:00-16:00, SOC still None or an outage still flagged, fewer than 5 attempts), retries 60 s later. Peak cover's own once-a-day logging is unchanged.

**Verification.** `tests/test_battery_health.py::test_peak_cover_startup_retry` and the updated `tests/test_code_integrity.py::test_startup_plan_is_followed_by_peak_cover`.

## Flexibility export events in shadow (05/11/2026, phase 77)

**Design.** Every 30 min, while `flex_event_entity` is on, `async_run_flex_event_shadow` logs once per event start (`_flex_event_logged`, unpersisted; the record dedup covers restarts).

`planning.flex_event_window` takes the event start and the hours left from a calendar's `start_time` / `end_time`. An on/off entity has no times, so it counts as starting now and lasting 1 h. Hours are clamped to 0.5–4.

`planning.flex_event_offer` reuses the `peak_export_plan` floor: evening reserve + the house load over the event (learned evening load, else the config average) + 5% margin. It applies the same worth-it test against the off-peak refill after round-trip losses and wear, and caps the offer at the export rate × hours. Decisions:
- `offer`, `no_surplus`, `not_worth_it` or `no_price`.
- `no_soc` when the SOC is unknown.
- `slots_held` while a free event holds the slots.

The record (`flex_event`: date, start time, entity, hours, soc, price, house load, decision, offer_kwh, value_gbp, gain_gbp, floor_soc) goes to the log, to `state.flex_event` and as JSON to the debug stream.

**Use.** Shadow only: nothing is exported and no option makes it live. Check the summer logs against an actual scheme's terms (eligibility alongside Flux, delivery obligations) before building a live path.

**Verification.** `tests/test_flex_event.py`:
- The offer is capped by the export rate and keeps the reserve floor; it is limited by surplus on a long event.
- The no_surplus / not_worth_it / no_price / no_soc / slots_held decisions, and wear tipping the decision.
- Calendar and on/off windows, caps, and naive times.

## Flux write safety (12/11/2026, phase 90)

**Root cause.** `async_push_flux_override` rebuilds every slot it is not given from the config baseline. A Flux 2 only push (trim, export pause, peak export) during a live peak-cover hold reset Flux 1 to the baseline while `_peak_cover["pushed"]` stayed true, so the hold was gone for the day. Separately, the Flux 2 paths read `evening_export_disabled`, awaited the push and only then set the flag, so two decisions could overlap.

**Design.**
- `planning.carry_held_flux1` adds the active hold slot to any push that names only Flux 2. `_async_push_flux2_action` applies it, so every Flux 2 push is covered. The hold stops being carried at 16:00.
- `planning.trim_target_soc(hold_soc=…)` floors the trim at the hold target, so a trim never sells energy the hold would buy straight back.
- Only active holds are carried. Flux 1 otherwise still falls back to the baseline (charge to 100%), which is the safe result if the next 01:55 push fails.
- `_flux2_lock` (an `asyncio.Lock`) wraps decide-and-push in the Flux 2 check, the 16:00 peak export, its re-checks and the full-charge trim. State is read inside the lock; notifications are sent after it is released.
- `api.py`: `_async_relogin` takes a login lock and skips the login when another caller has already replaced the stale token.

**Verification.** `tests/test_planning.py`: `test_carry_held_flux1_keeps_peak_cover_hold`, `test_trim_target_never_sells_below_a_hold`. The lock and login paths need HA and the cloud API, so they have no unit test.

## Measured round-trip efficiency, shadow (13/11/2026, phase 103)

- **Vetting.** `battery_etoday_charge/discharge` are only dashboard tiles (no log), so the measurement uses the BMS lifetime totals already in each `battery_health` record (`charge_total_kwh`, `discharge_total_kwh`, `cell_temp_c`). Those count energy at the battery terminals, so the measured figure excludes the inverter. The AC figure is the battery figure (capped at 1) times the assumed `CHARGE_EFFICIENCY` x `DISCHARGE_EFFICIENCY`; the inverter part is assumed, not measured.
- **Needed one new field.** `battery_health` records now carry `soc` (the live SOC when read), because the SOC correction needs the SOC at each sample. Earlier records lack it, so the first figure appears about three weeks after install (20 kWh charged in the 30-day window).
- **Measure.** `planning/efficiency.py`: between consecutive-day records, battery round trip = (discharged + SOC change / 100 x capacity) / charged, summed over the window (energy-weighted, not an average of daily ratios). Warm and cold groups split at 10 C cell temperature (each needs 10 kWh charged). A figure outside 0.5-1.05 is dropped as a unit or counter fault.
- **When.** Recomputed in the Sunday digest step over the 30-day window (the plan said monthly; weekly gets a first figure sooner), kept in `state.round_trip_measured` and logged as `round_trip_measured` (one per date).
- **Shadow line.** Every peak export and peak cover record carries `round_trip_shadow`: the gain under 0.85 against the gain under the measured AC figure, whether each is worth it, and the break-even efficiency (buy / sell price). Null until a measured figure exists. Never acted on: `ROUND_TRIP_EFFICIENCY` and `CHARGE_EFFICIENCY` still drive every decision.
- **Scorecard.** New `round_trip` feature: days with a shadow line, days the call would flip, and the £ the measured figure would have moved.
- **Decision to take later.** If the measured AC figure stays well above or below 0.85 over a winter, switch the constants in a separate phase.

## Inverter settings audit (13/11/2026, phase 107)

Weekly (Sunday 18:00) read-only snapshot of the inverter settings, diffed against the last one to catch changes made outside the integration. Separate from the drift check above, which compares against what was pushed. Design and deviations: `docs/plans/phases/107-inverter-settings-audit.md`.
