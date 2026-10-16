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

- The target is the evening reserve (phase 16) plus a 5% margin, capped at 100.
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
- Required SOC = floor + 75th-percentile recent `peak_load_kwh` (≥3 days, else 3 h × load) × 1.1 margin.
- `top_up` when the shortfall is ≥3 points and the day price beats the peak price after charge losses (`peak_cover_worth_it`); a missing price → `no_price`, never a decision to charge.
- The push waits until the charge would need to start (+45 min lead, `push_now`) so later solar can still help.

**Live (`peak_cover_live`, default off).** A Flux 1 slot now→16:00 with `targetSoc` = the required SOC. The inverter imports only while below the target, so it holds the battery there at the day rate rather than charging in one go. One push a day; at 16:00 `_async_peak_cover_end` (in `async_run_peak_export`) restores the normal plan (`_async_restore_normal_plan`). Never in monitor mode; skipped while a free event holds the slots.

**Shadow / logging.** One `peak_cover` record per day (dedup): the first actionable top-up, else the last check before 16:00, with the projection, required SOC, load/solar inputs, prices, `live` and `api_ok`; it rides the 22:00 debug bundle. Transient state `_peak_cover` is unpersisted by design: after a restart it re-evaluates.

**Known limits.** The load estimate before 07:00 falls back to the plan load, which is overnight-only and low. A Flux 1 slot replaces the stored import slot until the next push: the 16:00 restore re-pushes the last plan, and the 01:55 plan re-pushes every night.

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
