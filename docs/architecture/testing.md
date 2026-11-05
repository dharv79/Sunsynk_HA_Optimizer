# Testing

How to run the unit tests and iterate against a real Home Assistant.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Tests

The pure adaptive-learning logic in `data_logger.py` and the pure planning maths in `planning.py` have unit tests under `tests/` that run **without** a Home Assistant install — `tests/conftest.py` stubs `homeassistant.core` and loads `data_logger.py` directly by file path (bypassing the package `__init__.py` and its coordinator/optimizer import chain). Run them with:

```bash
pip install pytest && python3 -m pytest
```

`tests/conftest.py` exposes a `planning` fixture (the `planning` module), a `dl` fixture (a bare `DataLogger` instance for calling its pure methods) and a `make_day(**overrides)` helper for building paired-day dicts. When you change any `compute_*` / `count_*` / `_is_drain_night` / `_percentile` logic, or anything in `planning.py`, add or update a test that pins the new behaviour. The `.github/workflows/tests.yml` workflow runs `py_compile` + `pytest` on every push to `main` and every PR. Testing code that imports HA more deeply (optimizer, coordinator) requires mocking `hass`; prefer extracting pure helpers so they can be tested directly.

### Home Assistant iteration

Beyond the unit tests, development requires a real or dev Home Assistant instance:

1. Copy `custom_components/sunsynk_optimizer/` into the HA instance's `custom_components/` directory.
2. Restart Home Assistant or reload the integration via **Settings → Devices & Services**.
3. Check **Settings → System → Logs** for errors from the `custom_components.sunsynk_optimizer` logger.

When iterating on logic, the **Test plan (dry run)** button is the primary way to test without waiting for scheduled events: it recomputes the full import plan and posts the complete plan JSON to the HA app notification, with no inverter push, no data-log write, and no state change. The other manual buttons are **Reset baseline** (restore configured Flux windows) and **Update dashboard** (regenerate the Lovelace YAML).

To validate Python syntax without a running HA instance:
```bash
python3 -m py_compile custom_components/sunsynk_optimizer/*.py
```

## Test files

| File | Covers |
|---|---|
| `tests/conftest.py` | HA stubs, direct module loading, `dl`/`planning`/`flux_helpers`/`safe_id` fixtures, `make_day` |
| `tests/test_data_logger.py` | `compute_*` / `count_*` adaptive-learning maths, `_is_drain_night`, `_percentile`; version stamp on records |
| `tests/test_away_mode.py` | Home/away calibration split |
| `tests/test_day_actuals.py` | day_actuals / peak_window_usage logging and dedup; unavailable meters log None; charge_watchdog dedup |
| `tests/test_daily_cost.py` | daily_cost pairing, net cost, fill-only merge, month-boundary catch-up |
| `tests/test_planning.py` | `planning.py` target-SOC tree, bridge/ramp, charge rate, Flux 1 window, scoring, weekly period window, latest complete cost day, full-charge-day re-check, weighted forecast correction, learned household load, charge watchdog progress, charge efficiency and window grid kWh, evening reserve and trim floor; shadow scorecard verdicts (phase 41); unavailable-SOC plan retry and snapshot fallback (`soc_retry_due`, `recent_soc`); measured charge rate (phase 61) |
| `tests/test_kpis.py` | Phase 14 `day_kpis` / `week_kpis`, KPI prices, meter_snapshot dedup and pairing, `tools/backtest.py` replay and forecast-error report; phase 15 `import_feedback_adjustment`; phase 17 `arbitrage_worth_it` / `peak_export_plan` and peak_export pairing; phase 24 wear cost; phase 25 `expected_spare_solar`; phase 26 `baseload_anomaly`; phase 40 `daytime_import_anomaly`; phase 38 `marginal_cost_pence` / `cheapest_hour_today`; phase 37 `no_battery_baseline` / `battery_value_summary`; phase 34 `capacity_samples` / `capacity_estimate` / `capacity_low`; phase 36 `reconcile_month` / `logged_day_cost_gbp`; 85% SOC cap shadow (`soc_cap_shadow`) |
| `tests/test_flux_helpers_cost.py` | Peak import price behind the cost trigger (`band_price_pence_per_kwh`) |
| `tests/test_octopus_rates.py` | Phase 18 Octopus rate-entity window pricing, `tariff_prices_pence` fallback to `charges`, `price_source` labels |
| `tests/test_planning_auto_away.py` | Phase 27 `auto_away` (anyone home, min hours away, unavailable persons, calendar only, no signal) |
| `tests/test_clipped_solar.py` | Phase 29 `is_clipping`, `estimate_clipped_kwh` (missing → None), `overnight_target_too_high` |
| `tests/test_tariff_comparison.py` | Phase 28 band boundaries, `cost_under_tariff` (missing slot → None), `parse_half_hourly_charges`, `compare_tariffs`, rate overrides; phase 35 standing charge × days, VAT, Intelligent window, Agile slot pricing and unavailability, `agile_prices_by_date`, `newest_agile_product`, region/VAT overrides |
| `tests/test_gentle_charging.py` | Phase 23 `gentle_charge_current_a` (margin, clamps, zero need), `max_charge_current_a`, `gentle_current_lowers` (never above the user's Grid Amps), gentle fields on the logged import plan |
| `tests/test_meter_reconciliation.py` | Phase 44 `meter_reconciliation` (22:00 cut-off, tolerance, missing → None), `divergence_streak` |
| `tests/test_explain_plan.py` | Phase 58 `explain_plan` (one test per reason code, adjustments, shadow, missing data) |
| `tests/test_peak_export_recheck.py` | Phase 66 `peak_export_recheck` (lower/raise, 0.5 kWh gate, reserve floor, missing inputs) |
| `tests/test_free_event_dst.py` | Phase 51 DST-safe `elapsed_seconds`/`shift_hours`/`free_event_watch_from`, `free_event_in_watch` guard, sold battery/solar split |
| `tests/test_state_update_skip.py` | Phase 67 `state_fields_changed` (equal values skip the write, same object counts as changed); `test_code_integrity` also bars mutating `self.cfg` |
| `tests/test_newsvendor.py` | Phase 64 newsvendor costs, quantile bounds, `quantile_correction` (min days, band, cap), `newsvendor_target`, scorecard entry |
| `tests/test_evening_load.py` | Phase 55 learned 16:00-22:00 load (min days, bad meters, away skip, weekend split), shadow vs live reserve, clamp |
| `tests/test_intraday_scale.py` | Phase 56 intraday PV scale (10-11 damping, clamp, min forecast, missing data) |
| `tests/test_trim_defer.py` | Phase 65 trim overflow/defer split, no-forecast and clipped fallbacks |
| `tests/test_full_charge_skip.py` | Phase 68 days since last 99% SOC, due/skip, 14-day cap |
| `tests/test_import_causes.py` | Phase 42 import cause ordering, gaps, resets |
| `tests/test_data_audit.py` | Phase 45 audit rules, summary, config redaction |
| `tests/test_kpi_sensors.py` | Phase 47 KPI sensor values (None, never 0) |
| `tests/test_grid_outage.py` | Phase 48 grid voltage parser, runtime, outage record |
| `tests/test_pv_strings.py` | Phase 49 string share, anomaly, streak |
| `tests/test_user_override.py` | Phase 46 next plan date and override application |
| `tests/test_event_probe.py` | Phase 43 probe route list, tolerant realtime-event parser and dedup, cloud grid notices, fault counts |
| `tests/test_battery_health.py` | Phase 75 register-snapshot parser (real trimmed record), fault words, BMS stall hints, SOH trend and measured wear; peak-cover restart retry |
| `tests/test_upgrade_advisor.py` | Phase 50 upgrade advisor: `simulate_system` matches the phase 37 no-battery baseline at 0 kWh, size/PV/charge-rate/wear behaviour, `upgrade_advisor` scenarios, payback and `backtest.py --upgrade` |
| `tests/test_mpc.py` | Phase 69 shadow MPC: `mpc_solve` decisions (arbitrage, hold, wear, floor/rate, solar, export, terminal value), `mpc_slots`, `mpc_summary`, `mpc_backtest_day` and `backtest.py --planner mpc` |
| `tests/test_forecast_quantiles.py` | Phase 70 learned P10/P50/P90 solar: ratio samples, bin fallback, season wrap, walk-forward coverage, `scale_hourly`, plan field |
| `tests/test_load_profile.py` | Phase 71 load profile: `day_load_slots` shaping, `load_profile` day type/filters/summary, `heating_scale`, walk-forward backtest, MPC hand-off, `backtest.py --load-profile` |
| `tests/test_dynamic_tariff.py` | Phase 76 Agile shadow: `cheapest_window_plan` (ordering, partial slot, spike guard, negative slots, two days), `mpc_slots` price vectors, `dynamic_tariff_summary` |
| `tests/test_peak_cover.py` | Phase 30 `peak_cover_plan` (shortfall, timing, solar, prices), `solar_until_kwh`, `daytime_load_kw`, `peak_load_estimate_kwh`, `data_logger.latest_record` (22:00 report fallback) |
| `tests/test_dashboard.py` | Dashboard YAML structure |
| `tests/test_security.py` | `_safe_id` sanitising of entity IDs in generated YAML (b37 hardening) |
| `tests/test_code_integrity.py` | AST guard: every `self.<name>` read in `SunsynkOptimizer` is defined on the class (catches a dropped `def` line); the startup plan is followed by a peak cover re-check; every HA timer callback is an `async def` (a sync one runs off the event loop). CI also runs `pyflakes` |
