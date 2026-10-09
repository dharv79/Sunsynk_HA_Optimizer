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
