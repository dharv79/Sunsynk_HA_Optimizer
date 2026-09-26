# Entities and dashboard

Sensors, buttons, binary sensors, Lovelace YAML generator.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Entities

All entities extend `CoordinatorEntity` and read state from `coordinator.state` in their property methods. They receive updates only when the coordinator calls `async_set_updated_data`. No polling interval is set on the coordinator — updates are entirely event-driven.

- **Sensors** (`sensor.py`): expose `OptimizerState` fields; `import_plan_end` and `flux2_action` have rich `extra_state_attributes` exposing the full plan/action dicts. Four dedicated adaptive learning sensors read from `last_import_plan`: `forecast_correction`, `overnight_drain_adjustment`, `evening_soc_adjustment`, `effective_charge_rate` — each exposes `days_collected`, `days_required`, and `active` in `extra_state_attributes`. Thresholds are defined in `_ADAPTIVE_THRESHOLDS`. The `consumption` sensor (native value `"{day_load_kwh} kWh today"`) merges `last_morning_state`, `last_day_actuals`, `last_peak_window_usage`, `last_daily_cost`, and `last_year_to_date_cost` into one `extra_state_attributes` dict for the dashboard's Consumption section — field names don't collide (`peak_*`/`year_to_date_*` are distinctly prefixed) so it's a plain merge, except `last_daily_cost`'s own `date` is exposed separately as `daily_cost_date` since it's inherently a day behind the others (see below). `last_day_actuals` exists solely to give this sensor something to read; before v1.0.10b3 the 22:00 day-actuals capture was logged/notified but never persisted to `OptimizerState`.

- **Buttons** (`button.py`): call `optimizer` methods directly on press. `test_plan` calls `async_run_import_plan(source="test_button", dry_run=True)` — recomputes the full plan and notifies the JSON, but does not push to the inverter, log, or mutate state. Also `choose_best_day`, `reset_baseline`, `install_dashboard` (labelled "Update dashboard"). The old `run_import`/`run_flux2` push buttons were removed — pressing them mid-day pushed a daytime SOC reading to the inverter; the dry-run test replaces them. The `async_run_import_plan` / `async_run_flux2_check` methods still exist for the scheduled listeners.
- **Binary sensors** (`binary_sensor.py`): `evening_export_disabled` and `monitor_only` (derived from `operation_mode == "monitor"`).

### Dashboard

`dashboard_installer.py` generates a Lovelace YAML file by building a Python dict and serialising it with `json.dumps`. All SolarSynkV3 entity IDs are constructed from `inverter_serial` via the local `s()` helper. The file is written to `{hass.config.config_dir}/sunsynk_optimizer_{entry_id}.yaml` via `async_add_executor_job` (to avoid blocking the event loop), and a persistent HA notification shows the `configuration.yaml` snippet to add.
