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

### Spare solar (phase 25, 13/10/2026)

`planning.expected_spare_solar` (corrected hourly solar − house load, battery refilled first from the earliest surplus, 08:00–16:00) feeds `sensor.spare_solar_today` (kWh; attributes best 3-hour window start/end/kWh and a per-hour list). Refreshed ~90 s after startup and at 06:05; the 06:05 run sends "🔋 Sunsynk: spare solar today" only if `spare_solar_notify_kwh` is set and the total reaches it (blank = off). No forecast → total `None`, never 0. Read-only: no inverter writes. Tests: `tests/test_kpis.py`. Not refreshed on forecast change (06:05 and restart only).

### Overnight baseload alert (phase 26, 13/10/2026)

At 05:00 (after the meter snapshot) the day-load meter, which resets at midnight, gives the 00:00–05:00 house load. It is logged as `{"type":"overnight_load","date","load_kwh","away"}` (dedup per day). `planning.baseload_anomaly` compares it with the median of prior **home** nights (≥7 needed; away nights and `None`/≤0 readings ignored) and alerts when tonight ≥ 1.5× median and ≥ 1.0 kWh above it: "🔋 Sunsynk: overnight load high". Option `baseload_alert` (default on) disables the notification; logging continues. Read-only, so also runs in monitor mode. Missing meter → nothing logged, no alert.

### Daytime load spike alert (phase 40, 14/10/2026)

On the 30-minute tick between 05:00 and 16:00, `grid_pac` is sampled into an unpersisted per-day list. `planning.daytime_import_anomaly` alerts when the last 2 readings are both present and above 1.5 kW (0.75 kW in away mode) while SOC is above `BATTERY_LOW_SOC` + 5. The alert is "🔋 Sunsynk: daytime grid import", sent at most once a day. Samples reset while a free event holds the slots, or after peak cover pushed a top-up. Shares the `baseload_alert` option. Read-only, so also runs in monitor mode. Missing readings never alert. Tests: `test_daytime_import_*` in `tests/test_kpis.py`.

### Cost of 1 kWh now (phase 38, 14/10/2026)

`sensor.cost_of_1_kwh_now` (p/kWh) is what using 1 kWh right now costs. It is refreshed on the 30-minute tick and on every battery SOC change, and state is written only when the result changes. `planning.marginal_cost_pence` picks the source:
- **solar**: exporting more than 100 W and PV power covers the export. Cost = the export price the kWh displaces.
- **battery**: SOC above the reserve needed to cover the house from now (or 16:00) to 02:00 (`evening_reserve_soc` with those hours). Cost = off-peak price / 0.85 round-trip + wear (phase 24), or the current export price if higher, since that kWh could have been sold.
- **grid**: otherwise, and always in 02:00–05:00. Cost = the import band price (Octopus rates first, then `charges`).

A missing price gives `None`, never 0p. Attributes: `source`, `cheapest_hour_today` (with `cheapest_hour_pence` / `cheapest_hour_source`), `reserve_soc`. `planning.cheapest_hour_today` scans the remaining whole hours: spare-solar hours (from `sensor.spare_solar_today`) are priced at export, others at their import band; the earliest of equal minimums wins. Read-only. Shown on the dashboard under the spare solar card. Tests: `test_marginal_cost_*`, `test_solar_is_spare`, `test_cheapest_hour_today` in `tests/test_kpis.py`.

Example automation (run the dishwasher when cheap):

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.cost_of_1_kwh_now
    below: 16
condition:
  - condition: time
    after: "08:00"
    before: "20:00"
action:
  - service: switch.turn_on
    target: {entity_id: switch.dishwasher}
```

### Battery capacity estimate (phase 34, 14/10/2026)

Diagnostic `sensor.battery_capacity_estimate` (kWh): the measured usable capacity, with the `capacity_estimate` record as attributes. See import-plan.md.

The dashboard's "Bill check and battery health" card (Consumption column, after the cost cards) shows this sensor and `number.billed_last_month` with its reconciliation attributes. Both entity ids are pinned in code so the card matches (14/10/2026).

### Billed last month (phase 36, 14/10/2026)

`number.billed_last_month` (£, box entry): enter last month's billed net £ from the Octopus statement to reconcile it against the logged cost. See weekly-reports.md. Its attributes (`month` plus `planning.BILL_ATTRIBUTE_KEYS`) are always present, as `None` until a bill is reconciled. Otherwise Spook flags the dashboard's `logged_gbp`, `gap_gbp` and `days_logged` rows as unknown attributes (16/10/2026, `test_bill_attributes_always_has_every_key`).
