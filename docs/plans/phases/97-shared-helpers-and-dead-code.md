# Phase 97 — Refactor 1: shared helpers, dead code and missing tests

**Status:** Built (PR pending merge). **Size:** S-M, ~40-60k est. **Origin:** code review 11/11/2026 (refactor).

## Goal

Remove the duplicated maths before the files are split (phases 98 and 99), so the split moves one copy of each thing. No behaviour change.

## Findings

- Four percentile implementations (`planning._percentile`, inline in `peak_load_estimate_kwh`, `planning._quantile`, one in `data_logger.py`) and three medians. They differ on an empty list: one returns 0.0, one raises `IndexError`.
- The weighted-ratio builder and quantile walk are duplicated between `weighted_forecast_correction` and the newsvendor code.
- Three copies of "window, day-type match, minimum days, median" (`learned_load_kw`, `learned_evening_load_kw`, `load_profile`).
- HH:MM parsing is re-implemented five times beside `hhmm_to_minutes`.
- `optimizer.py`: the MPC input assembly is repeated almost line for line in `async_run_mpc_shadow` and `async_run_dynamic_tariff_shadow`; the `weather.get_forecasts` call and unwrapping appear twice; the outside-temperature-by-date comprehension appears twice.
- Dead code, referenced by tests only: `planning.grid_online` (production uses `grid_online_from_voltage`) and `planning.tou_settings_payload` (kept deliberately for phase 73 go-live: leave it, note it).
- No test references: `price_at`, `band_key_at`, `free_event_sold`, `mpc_load_kw`, `upgrade_scenarios`, `round_or_none`.

## Design

- One `percentile`, `median`, `weighted_quantile` and `hhmm_to_minutes` in `planning.py`; the others delegate and keep each caller's empty-list and failure fallback. `learned_load_kw` feeds the overnight target: pin its output with a before/after test first.
- One `_mpc_inputs()` helper and one `_weather_forecast()` helper in the optimizer.
- Remove `grid_online` and its test. Add tests for the six untested functions.
- Method: characterisation tests first (same inputs, same outputs), then the change.

## Saving

None. Smaller, safer base for phases 98 and 99.

## Built (12/11/2026)

- `planning.percentile` (optional `default` for an empty list; raises `ValueError` otherwise) and `planning.median`; `_percentile`, `_median` are aliases, `_quantile` and `peak_load_estimate_kwh`/`peak_load_estimate_split` delegate; `data_logger._percentile` delegates and keeps its 0.0-on-empty fallback.
- Five HH:MM re-parses in `planning.py` now use `hhmm_to_minutes`. `flux_helpers._parse_minutes` and `data_logger._flux1_end_hours` are left (different failure behaviour).
- `optimizer._mpc_inputs`, `_async_weather_forecast`, `_async_outside_temp_by_date`.
- `planning.grid_online` and its test removed; `tou_settings_payload` kept for phase 73.
- `tests/test_shared_helpers.py` covers the six untested functions and pins the shared maths.
- **Deferred:** the three copies of "window, day-type match, minimum days, median" (`learned_load_kw` feeds the overnight target) and the weighted-ratio builder shared with the newsvendor code. Both need a before/after fixture on real history; take them with phase 98.
