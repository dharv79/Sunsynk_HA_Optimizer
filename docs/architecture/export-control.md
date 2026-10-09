# Export control

16:00–19:00 export-disable: watt vs cost trigger, shadow mode, weekly tally.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Cost-aware export-disable threshold (v1.0.11 Part 3, shadow mode)

`async_run_flux2_check` computes two independent triggers for the 16:00–19:00 export-disable decision: `watt_trigger` (the original `grid_pac > CONF_EXPORT_DISABLE_THRESHOLD` check) and `cost_trigger` (`(grid_pac_kw × peak_import_price_pence_per_kwh) > CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR`, using `flux_helpers.peak_import_price_pence_per_kwh()` against the user's own configured `charges` — a pure helper, unit-tested without HA). `cost_trigger` is `None` outside the window or when `charges` has no matching import row for it — never coerced to a false 0p price. `CONF_COST_AWARE_EXPORT_SHADOW_MODE` (default `True`) gates which trigger actually drives the real decision: shadow mode on → `watt_trigger` always wins (zero behaviour change from pre-Part-3); shadow mode off → `cost_trigger` wins, falling back to `watt_trigger` if `cost_trigger` is `None`. Every check where the two triggers disagree is tallied on `self._shadow_export_stats` (unpersisted, same transient-state pattern as `_peak_window_start`) via `_tally_shadow_export_divergence` — `estimated_gbp_delta` only accumulates for the "cost says pause, watt didn't" direction, since the reverse carries no cost risk (export was already disabled). `_async_send_weekly_cost_summary` (Part 2) reads and resets this tally each Sunday, folding it into the weekly digest as `cost_aware_export_shadow_tally` so divergence is visible without a dedicated sensor. Default cost threshold (`DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR = 58.32`) is back-computed from the default Watt threshold (1.5 kW) × the default 16:00–19:00 import price (38.88 p/kWh), so a fresh upgrade stays behaviour-neutral even before the user touches the new config fields.


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
- `planning.arbitrage_worth_it` says to sell only if the peak export price beats the off-peak price ÷ 0.85 round-trip efficiency, plus battery wear. The wear term is `None` (no term) until phase 24 adds the options.
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
- While a successful live export holds Flux 2, the daytime and full-charge-day trims are skipped so they don't replace the window (`_peak_export_holds_flux2`, unpersisted `_peak_export`).
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
