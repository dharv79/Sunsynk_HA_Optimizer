# Testing

How to run the unit tests and iterate against a real Home Assistant.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Tests

The pure adaptive-learning logic in `data_logger.py` and the pure planning maths in `planning.py` have unit tests under `tests/` that run **without** a Home Assistant install — `tests/conftest.py` stubs `homeassistant.core` and loads `data_logger.py` and the `planning/` package directly by file path (bypassing the package `__init__.py` and its coordinator/optimizer import chain). Run them with:

```bash
pip install pytest && python3 -m pytest
```

`tests/conftest.py` exposes a `planning` fixture (the `planning` module), a `dl` fixture (a bare `DataLogger` instance for calling its pure methods), `DataLogger`, `flux_helpers` and `safe_id` fixtures, and a `make_day(**overrides)` helper for building paired-day dicts. When you change any `compute_*` / `count_*` / `_is_drain_night` / `_percentile` logic, or anything in `planning.py`, add or update a test that pins the new behaviour. The `.github/workflows/tests.yml` workflow runs `py_compile`, `pyflakes` and `pytest` on every push to `main` and every PR. Testing code that imports HA more deeply (optimizer, coordinator) requires mocking `hass`; prefer extracting pure helpers so they can be tested directly.

### Home Assistant iteration

Beyond the unit tests, development requires a real or dev Home Assistant instance:

1. Copy `custom_components/sunsynk_optimizer/` into the HA instance's `custom_components/` directory.
2. Restart Home Assistant or reload the integration via **Settings → Devices & Services**.
3. Check **Settings → System → Logs** for errors from the `custom_components.sunsynk_optimizer` logger.

When iterating on logic, the **Test plan (dry run)** button is the primary way to test without waiting for scheduled events: it recomputes the full import plan and posts the complete plan JSON to the HA app notification, with no inverter push, no data-log write, and no state change. The other manual buttons are **Run choose best day**, **Reset to baseline** (restore configured Flux windows), **Update dashboard** (regenerate the Lovelace YAML) and **Cancel free event**. The `get_plan` service returns the current plan as a response.

To validate Python syntax without a running HA instance:
```bash
python3 -m py_compile custom_components/sunsynk_optimizer/*.py
```

## Test files

| File | Covers |
|---|---|
| `tests/conftest.py` | HA stubs, direct module loading, `DataLogger`/`dl`/`planning`/`flux_helpers`/`safe_id` fixtures, `make_day` |
| `tests/test_data_logger.py` | `compute_*` / `count_*` adaptive-learning maths, `_is_drain_night`, `_percentile`; version stamp on records; phase 129: full-window/05:00-drain-source gating, unknown `morning_pv_power` exclusion, 05:00-capped calibration exclusion, `forecast_fallback` logging, truncated-line append |
| `tests/test_away_mode.py` | Home/away calibration split |
| `tests/test_day_actuals.py` | day_actuals / peak_window_usage logging and dedup; unavailable meters log None; charge_watchdog dedup |
| `tests/test_daily_cost.py` | daily_cost pairing, net cost, fill-only merge, month-boundary catch-up |
| `tests/test_planning.py` | `planning.py` target-SOC tree, bridge/ramp, charge rate, Flux 1 window, scoring, weekly period window, latest complete cost day, full-charge-day re-check, weighted forecast correction, learned household load, charge watchdog progress, charge efficiency and window grid kWh, evening reserve and trim floor; shadow scorecard verdicts (phase 41); unavailable-SOC plan retry and snapshot fallback (`soc_retry_due`, `recent_soc`); measured charge rate (phase 61); phase 127 export-pause restore/clear-slot, flux2-push-still-active, trim full-day flag, trim-slot clamp, reserve-hours-to-offpeak and peak-window-usage-from-snapshots |
| `tests/test_kpis.py` | Phase 14 `day_kpis` / `week_kpis`, KPI prices, meter_snapshot dedup and pairing, `tools/backtest.py` replay and forecast-error report; phase 15 `import_feedback_adjustment`; phase 17 `arbitrage_worth_it` / `peak_export_plan` and peak_export pairing; phase 24 wear cost; phase 25 `expected_spare_solar`; phase 26 `baseload_anomaly`; phase 40 `daytime_import_anomaly`; phase 38 `marginal_cost_pence` / `cheapest_hour_today`; phase 37 `no_battery_baseline` / `battery_value_summary`; phase 34 `capacity_samples` / `capacity_estimate` / `capacity_low`; phase 36 `reconcile_month` / `logged_day_cost_gbp`; `bill_period` (bill start day, 2.0.1); 85% SOC cap shadow (`soc_cap_shadow`); phase 120 `payback_summary`; phase 129 `week_kpis` sums `over_limit_import_kwh`, backtest `--load-kw` resize uses `bridge_soc` |
| `tests/test_flux_helpers_cost.py` | Peak import price behind the cost trigger (`band_price_pence_per_kwh`) |
| `tests/test_flux_helpers_merge.py` | Phase 128: `merge_entry_data` deep-copies `options`' nested lists too, so an in-place edit (tariff rows) never mutates `entry.options` itself |
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
| `tests/test_heatmap.py` | Phase 106 heat-map data (gaps, 95th-percentile scale, kinds, limits) and the card/websocket contract |
| `tests/test_twin.py` | Phase 72 digital twin: slot shaping and energy conservation, real-planner target and knobs, causal SOC run (charge, drain, surplus export, missing data → None), replay calibration and skips, sweep ranking, CLI; phase 129: `twin_target` reads the logged `is_full_day` directly |
| `tests/test_notify_policy.py` | Phase 108 title levels, quiet hours across midnight, send/hold rules, held list cap, morning brief |
| `tests/test_export.py` | Phase 109 export rows, range and type filtering, CSV columns, masking, daily-summary join |
| `tests/test_settings_efficiency.py` | Phase 112 efficiency flags (floors, bool settings, missing values, override), health flag count |
| `tests/test_daytime_hold.py` | Phase 114 slot covering now→16:00, hold floor rules, cap read-back; phase 122 reassert only below the hold, user raise kept |
| `tests/test_high_soc.py` | Phase 116 hours at ≥95% / 100%, gap handling, 30-day and year-to-date totals |
| `tests/test_bms_rate.py` | Phase 113 BMS-limited charge rate, resolve with BMS cap, rate source, plan fields |
| `tests/test_settings_audit.py` | Phase 107 snapshot filtering, diff (added/removed/numeric text), notification text; phase 119 `settings_dump` identifier masking; phase 128 `redact_settings_changes`; phase 129: `AUDIT_IGNORE_KEYS` drops Flux timer-slot keys but keeps the power-limit key |
| `tests/test_weather_load.py` | Phase 105 load/temperature fit, daily comparison, monthly flag and base shift; phase 129: `se_base_kwh` standard error, significance-gated `base_flag`, `load_report_text` keeps "kWh" capitalisation |
| `tests/test_shiftable.py` | Phase 102 recurring-block detection, valuation, fit check, cheap-window and bad-day filtering; phase 129: clusters anchor to each block's first slot, not its last |
| `tests/test_round_trip.py` | Phase 103 intervals, SOC correction, warm/cold groups, AC conversion, decision shadow, scorecard line |
| `tests/test_weekly_digest.py` | Phase 104 import causes by week, charge shortfalls, forecast error, verdict changes, weekly KPI sensors; phase 129: free-event kWh priced at 0 and excluded from the paid-kWh average |
| `tests/test_api_health.py` | Phase 101 API counters, failure classes, history, 3-day alert, weekly totals; phase 121 per-route failures, masked detail, read-back stop; phase 122 unsupported (404/405) route |
| `tests/test_daily_health.py` | Phase 100 health verdict, records-today count, deploy changes, record-type registry; phase 134 taper tolerance (`charge_shortfall_flagged`) |
| `tests/test_data_audit.py` | Phase 45 audit rules, summary, config redaction |
| `tests/test_kpi_sensors.py` | Phase 47 KPI sensor values (None, never 0) |
| `tests/test_grid_outage.py` | Phase 48 grid voltage parser, runtime, outage record |
| `tests/test_planning_package.py` | Phase 98 guard: every pre-split `planning` name (`tests/planning_names.txt`) is still exported and listed in `__all__`; `core` imports no sibling |
| `tests/test_shared_helpers.py` | Phase 97 percentile/median/hhmm characterisation; `band_key_at`, `price_at`, `free_event_sold`, `mpc_load_kw`, `upgrade_scenarios`, `round_or_none`; phase 129: NaN rejected by `_num`/`_is_number`/`percentile`/`median` |
| `tests/test_pv_strings.py` | Phase 49 string share, anomaly, streak |
| `tests/test_user_override.py` | Phase 46 next plan date and override application |
| `tests/test_event_probe.py` | Phase 43 probe route list, tolerant realtime-event parser and dedup, cloud grid notices, fault counts |
| `tests/test_battery_health.py` | Phase 75 register-snapshot parser (real trimmed record), fault words, BMS stall hints, SOH trend and measured wear; peak-cover restart retry |
| `tests/test_upgrade_advisor.py` | Phase 50 upgrade advisor: `simulate_system` matches the phase 37 no-battery baseline at 0 kWh, size/PV/charge-rate/wear behaviour, `upgrade_advisor` scenarios, payback and `backtest.py --upgrade` |
| `tests/test_mpc.py` | Phase 69 shadow MPC: `mpc_solve` decisions (arbitrage, hold, wear, floor/rate, solar, export, terminal value), `mpc_slots`, `mpc_summary`, `mpc_backtest_day` and `backtest.py --planner mpc` |
| `tests/test_forecast_quantiles.py` | Phase 70 learned P10/P50/P90 solar: ratio samples, bin fallback, season wrap, walk-forward coverage, `scale_hourly`, plan field; phase 82 cloud buckets, sparse-cell fallback, weather fields, `weather_bins` scorecard entry |
| `tests/test_hourly_profile.py` | Phase 86 hourly profile from timed samples, counter reset, reserve-hold split, scorecard entry, dedup |
| `tests/test_voltage_derate.py` | Phase 83 suspected high-voltage derating: threshold edges, curtailment test, missing inputs |
| `tests/test_battery_floor.py` | Phase 84 inverter low-SOC floor: key detection, floor-day £ saving, away skip, `None` inputs, dedup |
| `tests/test_load_profile.py` | Phase 71 load profile: `day_load_slots` shaping, `load_profile` day type/filters/summary, `heating_scale`, walk-forward backtest, MPC hand-off, `backtest.py --load-profile` |
| `tests/test_dynamic_tariff.py` | Phase 76 Agile shadow: `cheapest_window_plan` (ordering, partial slot, spike guard, negative slots, two days), `mpc_slots` price vectors, `dynamic_tariff_summary` |
| `tests/test_native_tou.py` | Phase 73 TOU shadow: `tou_slots_from_plan` mapping, padding, peak hold, merge, missing inputs; `tou_slots_from_settings` parsing; `tou_diff`; `tou_settings_payload` round trip; phase 115 `slot_power_w` floors and caps, `house_peak_kw`, `tou_inverter_powers` |
| `tests/test_flex_event.py` | Phase 77 flex event shadow: `flex_event_offer` floor/rate cap/decisions/wear, `flex_event_window` calendar and on/off entities |
| `tests/test_electrification.py` | Phase 78 electrification advisor: `heat_pump_cop`, `mean_band_price`, `heat_pump_scenario`, `export_limit_scenario`, `electrification_summary` |
| `tests/test_peak_cover.py` | Phase 30 `peak_cover_plan` (shortfall, timing, solar, prices), `solar_until_kwh`, `daytime_load_kw`, `peak_load_estimate_kwh`, `data_logger.latest_record` (22:00 report fallback); phase 122 `min_soc` / `peak_required_soc`; phase 123 `peak_load_recent_high`; phase 127 `peak_cover_retry_due`, `exclude_away_days` |
| `tests/test_peak_cover_split.py` | Phase 132 `peak_cover_plan_split` (generous hold, lean top-up, lean hold, no-price, Peak minimum SOC), `peak_load_typical`, `peak_need_soc` buffer |
| `tests/test_clear_notices.py` | Phase 133 `error_resolved`, `free_event_notice_shown`, `free_event_dismiss_due`; source pins for clear-on-success in `_guarded` and the Flux 2 check, `async_clear_notices` keeping the event; dashboard alert and Clear notices tile |
| `tests/test_no_buy_window.py` | Phase 135 `no_buy_start_minutes`, `no_buy_extra_load_kwh`, `peak_cover_last_log_minutes`, retry windows; source pins for the peak-cover window/slot/load and the end-at-start timer; dashboard picker |
| `tests/test_open_meteo_forecast.py` | Phase 136 `om_strings`, `compass_to_open_meteo`, `sun_position`, `om_reference_hours` hour alignment, `om_cloud_hours`, `hourly_counter_kwh`, `hourly_profile` pv_kwh, `om_training_hours`, `om_learn` shading, `om_predict`, `om_solar_start`, `daylight_cloud_cover`, the `open_meteo` scorecard row; source pins for the plan wiring, the off-without-config gate, the rounded location, the hourly cloud fallback and the options |
| `tests/test_dashboard.py` | Dashboard structure and views, sanitised ids, allowed custom cards, confirmed actions, no surrogate escapes, apexcharts keys, flow card and day chart config/JS, `card_resource` serves every bundled card |
| `tests/test_cards.py` | The bundled cards in Node (skipped without Node): the flow card redraws only for watched entities, the day chart follows today past midnight and a pointer move redraws only the cursor |
| `tests/test_security.py` | `_safe_id` sanitising of entity IDs in generated YAML (b37 hardening); phase 81: nested redaction, URL-quoted ids, card allow-list and `innerHTML`, safe override parsing, pinned workflow actions; phase 128: notify/report targets masked by exact key, phone/address keys |
| `tests/test_code_integrity.py` | AST guard (reads `optimizer.py` + `opt_*.py` as one class; also: API writes only in the two push helpers, mixins import no sibling): every `self.<name>` read in `SunsynkOptimizer` is defined on the class (catches a dropped `def` line); the startup plan is followed by a peak cover re-check; every HA timer callback is an `async def` (a sync one runs off the event loop); nothing mutates the cached `self.cfg`; sensor states are scalars and sensor keys unique. CI also runs `pyflakes` |
| `tests/test_monitor_guards.py` | Phase 125: monitor-mode backstop in both write helpers and its `allow_in_monitor` allowlist, Reset baseline refusal, export-pause flag following the push, full-day trim timing, dry-run skips (rule 17), monitor-aware health and audit; phase 129: drift-check reads return `None` not `[]` on failure and don't clear an episode, inverter fault falls back to last-known health, tariff alert ignores a wholly-unavailable Octopus read, the periodic drift timer owns its own callback, morning-brief release keeps mid-send held notifications |
| `tests/test_overnight_review_fixes.py` | Phase 126: outage handling in the charge watchdog and plan retry, the 01:55 autumn dedup and 02:00 DST catch-up, the forecast-fallback chain fix, the hourly-forecast today-only filter and the weekday weather local-time/first-occurrence fix — pinned by inspecting the optimizer's own source, since these are instance methods, not pure planning logic; phase 129: `heating_scale` uses yesterday's daily-mean temperature, `overnight_drain_adjustment` is computed before the `target_soc<100` gate |
| `tests/test_daytime_review_fixes.py` | Phase 127: export-pause restore hysteresis and `last_flux2_action`/tally gating, the 19:00 peak-window-usage snapshot delta (old tracker removed), peak-cover push retry, trim full-day flag and slot-end clamp, native-hold carry/restore and reassert-floor slot check, outage-aware and floored peak export/recheck, the `_flux2_lock` wraps, spare-solar's SOC-unavailable shape and the 22:00 `export_paused_date` capture — same AST-source technique as `test_overnight_review_fixes.py` |
| `tests/test_config_entities_review_fixes.py` | Phase 128: "blank = off" text fields use `suggested_value` not `default=`, `merge_entry_data` deep-copies `options`, monitor-mode adaptive sensors return `None` instead of the placeholder default, the settings-audit notification is redacted before it sends, the `config.error` translations match every code `_validate_input` can raise, and the heatmap/day-chart card fixes (deferred define, resize survives detach, local-time resample) — same source-pinning technique, plus plain text checks for the two `.js` files |
| `tests/test_capture_review_fixes.py` | Phase 129: `cost_days_from_records` doesn't require a plan/actuals join and keeps the last `daily_cost` record per date; `_guarded` returns its wrapped call's result (not always `None`) so the 22:00 sub-captures stay independent; `morning_pv_power` is `None` (not a fake 0 W sum) when either MPPT is unavailable |
| `tests/test_reports_review_fixes.py` | Phase 129: scorecard/shadow-scorecard load enough days for the 70-day capacity window, per-report monthly retry tracking, the £0-bill `TypeError` guard, bill reconciliation on `cost_days_from_records`, the dynamic battery-value history window, stale-digest resend guard, `export_data`'s CSV build runs off the event loop — AST source-pinning (`opt_reports.py` needs HA) |
| `tests/test_shadow_review_fixes.py` | Phase 129: the flex-event shadow dedups on the entity's `last_changed`, not the display time; the dynamic-tariff shadow matches the Flux horizon to a missing tomorrow-Agile vector — AST source-pinning (`opt_shadow.py` needs HA) |
| `tests/test_backtest.py` | Phase 129: `tools/backtest.py --load-kw` resize recomputes `bridge_soc` (margin, rounding, clamp) for both the logged and alternative load instead of a raw linear delta |
| `tests/test_free_event_review_fixes.py` | Phase 130: the sell-start/free-start/free-end transitions run under `_flux2_lock` (the restart race that wrapped Flux 1 to ~23 h and stuck one in "charging"), manual/dashboard scheduling now refuses in monitor mode, the restore push result drives the finished/cancelled notifications instead of always claiming success, a failed sell/free-start push retries (bounded) before advancing the phase, and the `schedule_free_event` service validates its times and surfaces a refusal — AST source-pinning (`opt_events.py`/`__init__.py` need HA) |
| `tests/test_refactor_review_fixes.py` | Phase 131: `async_run_import_plan` splits into `_async_rate_and_window`/`_async_shadow_forecast_fields`, the free-event timer plan dispatches `free_event_timer_plan`'s names to literal `self.attr` callbacks, and `SunsynkOptimizerSensor` registers/looks up units from one `_SENSOR_SPECS` table — AST source-pinning (`opt_overnight.py`/`opt_events.py`/`sensor.py` need HA) |
