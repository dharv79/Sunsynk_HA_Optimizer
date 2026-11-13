# Entities and dashboard

Sensors, binary sensors, buttons, switch, number, datetime entities; Lovelace YAML generator and bundled cards.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Entities

All entities extend `CoordinatorEntity` and read state from `coordinator.state` in their property methods. They receive updates only when the coordinator calls `async_set_updated_data`. No polling interval is set on the coordinator — updates are entirely event-driven.

- **Sensors** (`sensor.py`): expose `OptimizerState` fields; `import_plan_end` and `flux2_action` have rich `extra_state_attributes` exposing the full plan/action dicts. Four dedicated adaptive learning sensors read from `last_import_plan`: `forecast_correction`, `overnight_drain_adjustment`, `evening_soc_adjustment`, `effective_charge_rate` — each exposes `days_collected`, `days_required`, and `active` in `extra_state_attributes`. Thresholds are defined in `_ADAPTIVE_THRESHOLDS`. The `consumption` sensor (native value `"{day_load_kwh} kWh today"`) merges `last_morning_state`, `last_day_actuals`, `last_peak_window_usage`, `last_daily_cost`, and `last_year_to_date_cost` into one `extra_state_attributes` dict for the dashboard's Consumption section — field names don't collide (`peak_*`/`year_to_date_*` are distinctly prefixed) so it's a plain merge, except `last_daily_cost`'s own `date` is exposed separately as `daily_cost_date` since it's inherently a day behind the others (see below). `last_day_actuals` exists solely to give this sensor something to read; before v1.0.10b3 the 22:00 day-actuals capture was logged/notified but never persisted to `OptimizerState`.

- **Sensors** (`sensor.py`) also include the diagnostic and dashboard sensors listed in the dated sections below, plus `sensor.kpi_*` (`planning.KPI_SENSORS`).
- **Buttons** (`button.py`): call `optimizer` methods directly on press. `test_plan` calls `async_run_import_plan(source="test_button", dry_run=True)` — recomputes the full plan and notifies the JSON, but does not push to the inverter, log, or mutate state. Also `choose_best_day`, `reset_baseline`, `install_dashboard` (labelled "Update dashboard", entity id pinned to `button.install_dashboard`) and `cancel_free_event`. The old `run_import`/`run_flux2` push buttons were removed; the dry-run test replaces them. The `async_run_import_plan` / `async_run_flux2_check` methods still exist for the scheduled listeners.
- **Binary sensors** (`binary_sensor.py`): `evening_export_disabled`, `grid_outage`, `inverter_fault` and `monitor_only` (derived from `operation_mode == "monitor"`).
- **Switch** (`switch.py`): `away_mode`.
- **Number** (`number.py`): `number.billed_last_month` (see below).
- **Datetime** (`datetime.py`): `free_event_start` / `free_event_end`, the manual free electricity event window (see free-electricity.md).

### Dashboard

`dashboard_installer.py` generates a Lovelace YAML file by building a Python dict and serialising it with `json.dumps(..., ensure_ascii=False)`. All SolarSynkV3 entity IDs are constructed from `inverter_serial` via the local `s()` helper. The file is written to `{hass.config.config_dir}/sunsynk_optimizer_{entry_id}.yaml` via `async_add_executor_job` (to avoid blocking the event loop), and a persistent HA notification shows the `configuration.yaml` snippet to add.

Views, in order: Overview, Day chart (panel view), Plan, Energy & cost, Battery & system, Shadow lab, Controls. Custom cards: the bundled `custom:sunsynk-optimizer-flow-card` (Overview power flow) and `custom:sunsynk-optimizer-day-chart` (Day chart view), served by `card_resource.py` with no Lovelace resource needed, and `custom:apexcharts-card`, which still has to be installed from HACS. The slipx06 power-flow card is no longer used (`test_only_documented_custom_cards`).

### Dashboard revamp (05/11/2026)

Six sections views, one builder per view (`_overview`, `_plan`, `_energy`, `_system`, `_shadow`, `_controls`) on small card helpers (`_tile`, `_heading`, `_badge`, `_md`, `_apex`):

- **Overview** (path `sunsynk-optimizer`, kept so existing links work): view badges, alert cards shown only while their condition holds (grid outage, inverter fault, monitor-only, away, last error, free event), power flow, tonight's gauges and plan explanation, today's tiles, quick actions.
- **Plan**: "Why this plan?" table, P10/P50/P90 solar, learned load profile, adaptive learning.
- **Energy & cost**: today's meters, consumption windows, settled and year-to-date cost, import causes, bill check, KPI statistics graphs, 48 h history.
- **Battery & system**: SOH/BMS detail, capacity, runtime, system health, live inverter values, logbook.
- **Shadow lab**: scorecard, MPC SOC/grid chart, Agile vs Flux, TOU slot check, flex event offer, upgrade advisor.
- **Controls**: away mode, test plan, one-night full charge / skip (`sunsynk_optimizer.*` actions, always with a confirmation), reset, update dashboard, free event entry, install details.

Attribute values are read in markdown through the `_MACROS` Jinja helpers `a()` / `gbp()`, which print "—" for a missing value (never 0). Entities-card attribute rows are only used for always-present attributes (Spook). Config entity ids interpolated into templates pass `_safe_entity`. Charts of attribute arrays (MPC path, load profile, spare solar by hour) use `custom:apexcharts-card` `data_generator`. At the time the only custom cards were it and the bundled flow card; the day chart was added in phase 80 (below).

New diagnostic sensors for the dashboard: `sensor.tou_shadow` (state = slot differences, attributes the phase 73 record), `sensor.flex_event_offer` (state = decision, attributes the phase 77 record) and `sensor.upgrade_advisor` (GBP, best annual saving; attributes `options` from `planning.upgrade_options`, best first). Fixed with it: phase 76 had left `sensor.mpc_plan`'s state as its whole dict and `sensor.dynamic_tariff_shadow` unknown; `test_sensor_states_are_scalars_and_keys_unique` guards both.

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

### Shadow scorecard (phase 41, 01/11/2026)

Diagnostic `sensor.shadow_scorecard`: state is the number of shadow features with verdict `ready`; attributes hold one entry per feature (verdict, days, changed, `delta_gbp`, evidence dates, note). Built monthly (first Sunday, 18:00) by `planning.shadow_scorecard` from the raw log; read-only, never flips a `*_live` option. Verdict rules are in the phase 41 file.

### Battery capacity estimate (phase 34, 14/10/2026)

Diagnostic `sensor.battery_capacity_estimate` (kWh): the measured usable capacity, with the `capacity_estimate` record as attributes. See import-plan.md.

The dashboard's "Bill check and battery health" card (Consumption column, after the cost cards) shows this sensor and `number.billed_last_month` with its reconciliation attributes. Both entity ids are pinned in code so the card matches (14/10/2026).

### Billed last month (phase 36, 14/10/2026)

`number.billed_last_month` (£, box entry): enter last month's billed net £ from the Octopus statement to reconcile it against the logged cost. See weekly-reports.md. Its attributes (`month` plus `planning.BILL_ATTRIBUTE_KEYS`) are always present, as `None` until a bill is reconciled. Otherwise Spook flags the dashboard's `logged_gbp`, `gap_gbp` and `days_logged` rows as unknown attributes (16/10/2026, `test_bill_attributes_always_has_every_key`).

## Device, KPI sensors, services (04/11/2026, phases 46-49)

- **Device (47).** `entity.optimizer_device_info` (identifiers = inverter serial, rule 2) is set on every platform's entities. New entities set an explicit `entity_id`; existing ones keep their registry ids.
- **KPI sensors (47).** `sensor.kpi_*` from the latest `day_kpis` (`last_day_kpis`): daily £ are MONETARY/TOTAL with `last_reset`, the rest MEASUREMENT. Unknown, never 0.
- **Others.** `sensor.health` (100), `sensor.api_success_rate` (101), `sensor.data_health` (45), `binary_sensor.grid_outage` and `sensor.battery_runtime` (48), `sensor.pv_string_share` (49).
- **Battery SOH (75).** `sensor.battery_soh` (%, diagnostic) is the BMS state of health from the cloud register snapshot; attributes carry packs, cycles, capacity, cell spread/temperature, BMS limits, fault words and the SOH trend.
- **MPC plan (69).** `sensor.mpc_plan` (diagnostic): state is the shadow MPC's next half-hour action (`grid_charge`, `solar_charge`, `discharge`, `export`, `hold`); attributes carry the summary, the rule plan's target and the 24 h per-slot SOC path.
- **TOU check (73), flex offer (77), upgrade advisor (50/78).** `sensor.tou_shadow`, `sensor.flex_event_offer`, `sensor.upgrade_advisor` (diagnostic); see the dashboard revamp section.
- **Agile vs Flux (76).** `sensor.dynamic_tariff_shadow` (diagnostic, GBP): state is the nightly shadow saving of the same 24 h MPC on Agile half-hour prices compared with Flux (positive = Agile cheaper). Attributes hold both costs, price min/max, spike and negative slots, and the cheapest charge windows.
- **Solar forecast P50 (70).** `sensor.solar_forecast_p50` (kWh, diagnostic): today's learned median solar from the last plan; attributes carry the P10/P90, ratios, bin, days and raw forecast.
- **Load forecast (71).** `sensor.load_profile` (kWh, diagnostic): today's learned house load total; attributes carry the 48 half-hour slots, days used, day type, temperature scale and the peak/evening/day bands.
- **Inverter fault (43).** `binary_sensor.inverter_fault` is on while the Sunsynk realtime event list is non-empty or the cloud counts a fault/protect inverter or a register fault word is set; attributes carry the events and counts. Read from the 2-hour drift check and the charge-watchdog recheck.
- **Services (46, 43).** `set_tonight_target`, `skip_tonight`, `schedule_free_event`, `cancel_free_event`, `get_plan` (response only), `probe_inverter_events` (response only, read-only). `services.yaml` + `strings.json`. Overrides apply at the next 01:55 plan; writes refuse in monitor mode and while a free event holds the slots.

### Dashboard checked in a real Home Assistant (05/11/2026)

The generated file was loaded in HA 2025.10 with the two HACS cards and sample states, at 1440 px and 390 px (Playwright). It found three bugs the unit tests could not:

- **Emoji escapes.** `json.dumps` escapes emoji as surrogate pairs, which HA's YAML loader rejects ("surrogates not allowed"), so the whole dashboard showed "Unknown error". `_render` uses `ensure_ascii=False` (`test_rendered_file_has_no_surrogate_escapes`).
- **`grid_options` on apexcharts-card.** v2.1.2 validates strictly and turned all three charts into error cards; custom cards default to full width, so none is set (`test_apexcharts_cards_have_no_unknown_keys`).
- **Truncated tile names** at section width; tile names are kept to about 12 characters.

All six views then rendered with no error cards, no console errors and no horizontal scroll at both widths. Markdown tables, the charts and the confirmation-guarded actions were checked visually.


## Own power-flow card (05/11/2026, phase 79)

- `www/sunsynk-optimizer-flow-card.js` replaces the HACS slipx06 card. `card_resource.async_register_card` (called from `async_setup`) serves it at `/sunsynk_optimizer_static/…js?v=<manifest version>` and loads it on every dashboard with `add_extra_js_url`, so no Lovelace resource is needed. It never raises: a failure only logs a warning. Manifest `dependencies` now list `http` and `frontend`.
- The card is built once and patched in place on each state change, so the flow animation never restarts. Sign conventions: `grid_pac` > 0 import, < 0 export; `battery_power` > 0 discharge, < 0 charge (`invert_battery`/`invert_grid` flip them). Missing or unavailable readings show "—" and stop the flow, never 0.
- Config written by `dashboard_installer._power_flow_card`: `entities` (PV1/2, load, UPS, grid, SOC, battery power, capacity estimate, daily meters) and `extras` (tonight's target SOC, charge window, cost of 1 kWh now, forecast, evening export, outage and fault flags). Hub ring = share of the home's load not coming from the grid; battery bar carries a marker at tonight's target and an ETA to full or to the reserve (needs `battery_kwh` or the capacity estimate).
- Tests: `test_flow_card_*` in `tests/test_dashboard.py` (config keys all read by the JS, element registered). Real-HA check: Chromium at 1440 and 390 px.

## Day chart tab and bundled-card load race (05/11/2026, phase 80)

- `www/sunsynk-optimizer-day-chart.js` (`custom:sunsynk-optimizer-day-chart`) draws one day of PV (MPPT 1 + 2), battery, load and grid power (W, left axis) and SOC (%, right axis), like the Sunsynk app's Generation chart. ◀ / ▶, a date picker and Today move between days; tap or hover reads values at that time (5-minute bins); legend items toggle series. Day totals are each daily counter's last reading before midnight.
- Data: `history/history_during_period` (minute detail; the recorder keeps 10 days by default). When a day has no detail it falls back to `recorder/statistics_during_period` hourly means and says so; with neither it says there is no data. History is fetched per day, not per state change; today refreshes every 5 minutes. Days are the browser's local days; DST days are drawn on a 24 h axis.
- `dashboard_installer._day_chart` adds the "Day chart" tab after Overview as a **panel** view (a one-section sections view stays one column wide on desktop). `card_resource` now serves the whole `www/` folder and adds every file in `CARD_FILES`.
- **Load race (root cause of the 05/11 "Configuration error" on the flow card):** the frontend swaps in a scoped custom-element registry while it boots; an element defined before that is invisible to it, so the card never upgrades. Both cards now call `customElements.define` only after `customElements.whenDefined("home-assistant")` (10 s fallback). Verified: 10/10 fresh loads define both cards (previously intermittent).
- Tests: `test_day_chart_*`, `test_card_resource_serves_every_bundled_card`. Real-HA check: HA 2025.10.1 with back-filled recorder rows (today, yesterday, 3 days ago) and imported hourly statistics (15 days ago), Chromium at 1440 and 390 px.

## Fewer state writes (12/11/2026, phase 92)

**Root cause.** Every battery SOC change stored a `battery_runtime` dict that always differed, so the whole state (plans, 48-slot `mpc_plan`, health ledger, the last notification's full text) was re-saved each time. Above 85% SOC the Flux 2 check's no-trigger branch also stamped `updated_at` on every event.

**Design.**
- `coordinator.update_state(persist=False)` updates the entities but skips the storage write. Used only for `battery_runtime`, `marginal_cost` and `import_causes`, which are rebuilt live; a test pins that list. The values are still saved with the next persisting change.
- The Flux 2 no-trigger branch stamps and saves only when it clears an export pause.
- `last_notification` keeps the title, the first 500 characters and `message_chars` (the full length).
- `planning.clip_text` caps the `last_error`, import plan and Flux 2 action sensor states at 255 characters; the full error is in the `full_error` attribute when it was cut.

**Verification.** `tests/test_state_update_skip.py`: `test_clip_text_fits_the_entity_state_limit`, `test_update_state_persist_flag_is_only_used_for_derived_fields`.

## Stable entity IDs and statistics (12/11/2026, phase 94)

**Root cause.** Entities have `has_entity_name = True` and a device named "Sunsynk Optimizer", so Home Assistant names a new entity `<domain>.sunsynk_optimizer_<name>` unless the ID is set. Only the newer entities set it, so on a new install the generated dashboard pointed at IDs that did not exist. Numeric sensors had no `state_class`, so they kept no long-term statistics.

**Design.**
- `entity.pin_entity_id` sets every entity's ID: the explicit ID where one was given, else `<domain>.<slug of the name>` (`planning.entity_slug`), which is the ID older installs already have. It only affects a new registration; the registry keeps existing IDs.
- `pin_entity_id` also records `unique_id → dashboard ID` in `entity.DEFAULT_ENTITY_IDS`. `dashboard_installer._registry_id_map` looks each one up in the entity registry and `planning.remap_entity_ids` rewrites the rendered dashboard where the real ID differs (prefixed IDs from an install made before this fix, renamed entities, a `_2` collision).
- `sensor._MEASUREMENT_KEYS` get `SensorStateClass.MEASUREMENT`. `current_soc_target` and the two SOC adjustments gain `%`; the effective charge rate gains `kW`. Daily kWh and £ sensors are unchanged.

**Not verified on a clean Home Assistant.** The naming rule is Home Assistant's documented behaviour; the fix was not run on a fresh install.

**Verification.** `tests/test_dashboard.py`: `test_every_optimizer_id_in_the_dashboard_is_pinned`, `test_pin_entity_id_uses_the_name_or_the_explicit_id`, `test_remap_entity_ids_replaces_whole_ids_only`.

## Card rendering: redraw only on change (12/11/2026, phase 95)

**Root cause.** The flow card redrew on every state change anywhere in Home Assistant. The day chart rebuilt the whole SVG (five series of 288 bins) on every pointer move, and a chart left open on "today" stayed on the old day after midnight with Next and Today disabled.

**Design.**
- Flow card: `setConfig` lists the entities the card reads (`_watch`); `set hass` returns early when every watched state object is the same object as last time.
- Day chart: a pointer move inside the same 5-minute bin does nothing; a new bin calls `_drawCursor`, which updates only the cursor line, time label and legend values inside a `<g data-cur>` group.
- Day chart: `_tick` (every 5 minutes) moves a card that was following today to the new day, and re-enables Next and Today on a card showing an older day.
- No version constant to bump: the cards are served with `?v=<manifest version>`, so browsers reload them on the next release.

**Verification.** `tests/test_cards.py` runs both cards in Node. Checked once in Chromium: three pointer moves in one bin gave one cursor update and no full redraw.

## Daily health line and deploy notice (13/11/2026, phase 100)

- **Health.** At 22:00 `opt_monitor._async_capture_daily_health` builds a `daily_health` record (pure `planning.daily_health`) and puts it first in the debug bundle. It also feeds `sensor.health` (OK / Check / Problem; unknown when a core input is missing, never OK). Problem: last push failed, or no SOC update for 6 h. Check: any `_guarded` error, API failure, charge more than 5 points short, fewer than the 4 daily records, or SOC older than 2 h. Attributes carry version, reasons, records written/expected, errors by label, API failures, SOC age and active shadow features. Not written to the JSONL log (never read back).
- **Counters.** `_guarded_errors` (by label) is unpersisted and reset each 22:00; a restart only thins the day's count. The API failure count now comes from the phase 101 counters (below), so health and the API log agree.
- **Deploy notice.** 45 s after startup `async_run_deploy_check` compares `deployed_version` with the manifest. On a change it sends a `deployed` record to the debug target and, if a previous version was known, a notification listing the record types new since the last deploy (`data_logger.RECORD_TYPES` against the stored `known_record_types`). Fields within a type are not tracked. The first run after this phase has no stored list, so `new_record_types` is null.
- **Registry.** `RECORD_TYPES` is kept by hand; `tests/test_daily_health.py` fails if a type the logger writes is missing from it.

## Cloud API reliability log (13/11/2026, phase 101)

- **Counting.** `api.py` times and counts every `_authorized_json` call (login, 401 re-login and retry included) in `SunsynkApiClient.stats` (`planning.ApiStats`, pure): calls, failures by kind (`timeout`, `401`, `5xx`, `other`), re-logins after an expired token, median and worst latency. The plan retry (`async_run_plan_retry`) adds `push_retries`. No push logic changed. Counters are unpersisted (a restart thins the day) and reset by the 22:00 `take`.
- **Reporting.** 22:00 `_async_capture_api_health` puts an `api_health` line second in the bundle, keeps a 14-day history in state (`api_health_days`), feeds `sensor.api_success_rate` (diagnostic, %, attributes = the day's counters) and the weekly summary's `api_health_week` totals.
- **Alert.** One Repairs issue `api_unreliable` when the failure rate exceeds 10% (at least 5 calls) on 3 consecutive days; deleted when the streak breaks.
- **Phase 74 evidence.** Read the weekly `api_health_week` and the `api_health` lines over a few weeks before deciding on local Modbus.
