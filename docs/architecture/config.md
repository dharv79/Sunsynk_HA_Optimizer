# Config and operation modes

Config entry split, ID distinction, options flow, auto/monitor modes.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Config entry split

Credentials (`username`, `password`, `plant_id`, `inverter_serial`) live in `entry.data`. All other settings (`charges`, `flux_products`, thresholds, forecast entity, etc.) may live in either `entry.data` (initial setup) or `entry.options` (reconfiguration). Always call `merge_entry_data(dict(entry.data), dict(entry.options))` to read config — never read `entry.data` or `entry.options` directly in logic code.

### Key ID distinction

`plant_id` — numeric Sunsynk API plant/station ID used in all API calls.  
`inverter_serial` — alphanumeric serial used to build SolarSynkV3 sensor entity IDs like `sensor.solarsynkv3_{inverter_serial}_battery_soc`.  
These are different values and must never be swapped.

### Options flow

Setup is one `user` step (credentials, `inverter_serial`, `plant_id` plus the main settings; login is validated). The options flow is multi-step: `init` (all main settings except credentials) → `charges_1` (tariff rows 1–4) → `charges_2` (tariff rows 5–8) → `flux` (baseline Flux windows). State is accumulated in `self._working` (seeded from `merge_entry_data`) across steps before being saved on the final step, which reloads the entry. An optional field emptied in `init` is dropped, so "blank = off" sticks.

Main settings (`config_flow._base_schema`, defaults in `const.py` / `planning.py`): weather entity, solar forecast sensor, notify service/target, `operation_mode`, currency/invest (Sunsynk portal), `export_disable_threshold` (1500 W), `export_disable_cost_threshold_pence_per_hour` (58.32), the shadow/live toggles below, `baseload_alert` (on), `default_full_charge_day` (Wednesday), battery capacity (10 kWh), charge rate (3 kW), `battery_voltage` (51.2 V), free event charge/export rate, battery cost and lifetime (wear), upgrade/PV/flex/gas/heat-pump inputs, `spare_solar_notify_kwh`, average/weekend/away consumption, auto-away fields, `export_limit_w` (0), solar start offset (2.5 h), hourly/tomorrow forecast sensors, Octopus cost/consumption/rate entities, `bill_start_day`, `tariff_rate_overrides`, `data_report_target`, `enable_ai_weekly_insight` (off).

### Operation modes

`auto` — full optimizer behaviour, API writes enabled.  
`monitor` — all three main logic paths (`async_run_import_plan`, `async_run_flux2_check`, `async_choose_best_full_charge_day`) return early without making API calls. Live peak export, peak cover and gentle charge stay shadow-only, and service writes are refused. Read-only features (alerts, reports, shadows, fault reads) still run. The 05:00 grid-charge current restore is the one write that still runs.


## Shadow-first options

- `cost_aware_export_shadow_mode` (default on, phase 3): see export-control.md.
- `import_feedback_live` (default off, phase 15): when off, the day-rate import feedback is only logged; when on, it replaces the evening SOC nudge. See import-plan.md.
- `peak_export_live` (default off, phase 17): when off, the 16:00 peak surplus export decision is only logged; when on, Flux 2 exports 16:00–19:00 down to the evening reserve + the expected 16:00–19:00 use + 5% (b25). See export-control.md.
- `gentle_charge_live` (default off, phase 23): when off, the gentle grid-charge current is only logged; when on, the 01:55 plan writes it, keeps Flux 1 open to 05:00 and restores the previous current at 05:00. `battery_voltage` (default 51.2 V) converts kWh to amps. See import-plan.md.

- `peak_cover_live` (default off, phase 30): when off, the midday "will the battery last the peak window" projection is only logged; when on, a Flux 1 slot now→16:00 holds the battery at the SOC the peak needs, at the day rate. See export-control.md.

- Other live toggles, all default off and shown in the options flow: `full_window_live` (phase 62, Flux 1 to 05:00 every night), `measured_charge_rate_live` (phase 61), `evening_load_live` (phase 55), `load_profile_live` (phase 71), `trim_defer_live` (phase 65), `full_charge_skip_live` (phase 68). See import-plan.md / export-control.md.

## Octopus rate entities (phase 18)

`octopus_import_rates_entity` / `octopus_export_rates_entity` (optional, free text): an Octopus Energy current-rate sensor or day-rates event. Blank keeps pricing on `charges`. See export-control.md.

## Saving sessions (phase 21) — removed 16/10/2026

`saving_session_reward_pence` and `octopus_saving_session_entity` are gone from the options flow. Stale values left in `entry.options` are ignored. See free-electricity.md.

## Automatic away mode (13/10/2026, phase 27)

Optional: `auto_away_persons` (comma-separated `person.*`), `auto_away_calendar` + `auto_away_keyword`, `auto_away_min_hours` (default 24). `planning.auto_away` returns True/False/None; `optimizer._async_auto_away` runs at 22:00 and before the 01:55 plan, sets `away_mode` via `update_state` only on a change, and notifies. Anyone home → home at once; away needs all known persons away ≥ N hours (from the state's `last_changed`) or an active matching calendar event. Unavailable persons are ignored; no signal (None) leaves the manual switch alone. No config → no-op.

## Options added 05/11/2026 (phases 50, 71)

`upgrade_price_per_kwh`, `pv_kwp`, `pv_upgrade_price_per_kwp` (optional, upgrade advisor payback and PV scenario) and `load_profile_live` (default off: evening reserve and peak cover from the learned half-hourly profile).

`flex_event_entity` (optional calendar or on/off entity; on = a paid export event) and `flex_price_pence` (optional p/kWh the event pays) for the phase 77 flex event shadow. Both are blank by default, which turns it off.

`gas_unit_pence`, `gas_standing_pence` and `heat_pump_price_gbp` (all optional) feed the phase 78 heat-pump scenario in the monthly upgrade report. Without `gas_unit_pence` it doesn't run.

## Bill period start day (2.0.1)

`bill_start_day` (optional, 1–28, default 1). The bill check (`number.billed_last_month`) reconciles the latest complete period before today, from that day to the day before it next month (`planning.bill_period`, values clamped 1–28). Day 1 is the previous calendar month, labelled `YYYY-MM`; other days are labelled `YYYY-MM-DD to YYYY-MM-DD`. A reconciliation stored before this option (month only) is read as a calendar month.

## Options validation and redaction by value (12/11/2026, phase 93)

**Root cause.** Tariff and Flux times were free text. A value such as `16.00` made `kpi_prices_pence` raise on every run and went verbatim into each income POST. Diagnostics masked by key name only, so identifiers inside entity IDs, error URLs and `paired_days` passed through.

**Design.**
- `planning.time_field_errors` checks every time field against 24-hour `HH:MM`. The options steps `charges_1`, `charges_2` and `flux` re-show the form with `invalid_time` or `same_time`. Start = end is rejected for the Flux windows only: a tariff row of `00:00`-`00:00` is a valid all-day band.
- Flux targets in the form are 20-100, matching the service action.
- `optimizer._check_config_times` runs at setup and raises the `config_bad_time` Repairs issue for a bad time that was saved before this check existed; it clears itself once fixed.
- `planning.redact_diagnostics` masks by key as before, then replaces every sensitive value (`planning.sensitive_values`: values under masked keys, the MPAN and meter-serial parts of Octopus entity IDs, person and calendar entities) in every string of config, state and `paired_days`. Values under 4 characters are not masked by value.

**Verification.** `tests/test_security.py`: `test_time_fields_must_be_hhmm`, `test_config_time_problems_lists_bad_saved_times`, `test_diagnostics_redacts_identifiers_by_value`. The form steps themselves need HA.

## Notification priorities (13/11/2026, phase 108)

Options `notify_min_level` (default report = send everything), `notify_quiet_start`/`notify_quiet_end` and `morning_brief_time` (blank = off). Levels are derived from the notification title in `planning.notification_level`; action-needed messages always send; held messages go out in one morning brief. The debug stream and any explicit-target send are never held. See `docs/plans/phases/108-*.md`.

## Review fixes: config, entities, diagnostics and cards (22/11/2026, phase 128)

**"Blank = off" wasn't actually clearable.** Every optional text field used `vol.Optional(key, default=values.get(key, ""))`. HA's `FlowManager` re-validates a submitted form against the `data_schema` that rendered it; when the frontend omits a field the user cleared, voluptuous reinserts that schema's `default` — which here was the *old stored value* — before `async_step_init` ever sees `user_input`. So the step's own "drop a key missing from `user_input`" logic (`config_flow.py`, the `_working.pop` loop) never fired for these fields. Fixed by switching them to `description={"suggested_value": values.get(key)}` (no `default`), the same pattern the numeric optional fields already used: `flex_event_entity`, `auto_away_persons`/`auto_away_calendar`, `hourly_forecast_sensor`, the six Octopus sensor/entity fields, `tariff_rate_overrides`, `data_report_target`, `notify_quiet_start`/`notify_quiet_end`, `morning_brief_time`. Fields whose blank value should fall back to a compiled default rather than mean "off" (`auto_away_keyword`, `hourly_forecast_attribute`, `tomorrow_forecast_sensor`) keep `default=`.

**Options-flow edits to tariff rows were silently dropped.** `flux_helpers.merge_entry_data` deep-copied `entry.data` but not `entry.options`, so `self._working[CONF_CHARGES]` was the *same list object* as `entry.options[CONF_CHARGES]`. `_save_charge_rows` edits that list in place (`charges[idx] = {...}`), which therefore mutated `entry.options` itself — by the time `async_create_entry(data=self._working)` ran, the "before" and "after" compared equal and HA skipped saving and reloading. Fixed by deep-copying `options` too before merging.

**Diagnostics and the settings-audit notification didn't mask everything they should.** `redact.py`'s `_AUDIT_REDACT_PARTS` and `planning/settings_audit.py`'s own `_DUMP_REDACT_PARTS` had diverged (one had `phone`/`address`, the other `plant_id` instead of the broader `plant`); unified on the one list in `redact.py`. Added an exact-match `_AUDIT_REDACT_KEYS` set (`notify_service`, `notify_target`, `data_report_target`) so the diagnostics download masks those destinations too — a substring match on "target" would also catch legitimate numeric fields like `current_soc_target`, so it has to be exact. The weekly settings-audit notification (`opt_monitor.async_run_settings_audit`) compared raw inverter settings and sent the diff straight to `async_notify`/`async_notify_debug` without ever going through `redact_diagnostics`; added `planning.redact_settings_changes` (mirrors `settings_dump`'s identifier-by-value masking) and call it before either send.

**Monitor mode fed made-up numbers into long-term statistics.** `async_run_import_plan` stubs `last_import_plan` to `{"logic_branch": "monitor_only", ...}` in monitor mode — it never gets a real push to learn from. The adaptive sensors (`forecast_correction`, `overnight_drain_adjustment`, `evening_soc_adjustment`, plus their `days_collected`/`active` attributes) read that stub with `.get(key, default)`, so the placeholder meant for "before the first plan ever runs" instead stood in forever and was recorded as a flat, fake `MEASUREMENT` reading. `sensor.py`'s `native_value` now returns `None` for these when the key is missing *and* `state.operation_mode == "monitor"`; outside monitor mode the placeholder still applies.

**Dashboard cards.** `sunsynk-optimizer-heatmap.js` defined its custom element immediately at script-parse time, which can lose the race with HA's scoped custom-element registry and show "Configuration error" (the same race phase 80 fixed in the day chart); now deferred behind the same `customElements.whenDefined("home-assistant")` pattern. It also disconnected its `ResizeObserver` on `disconnectedCallback`, which is never recreated on an SPA reattach (detach/reattach reuses the element), so resize stopped redrawing after one tab switch; the observer is no longer disconnected there. `sunsynk-optimizer-day-chart.js`'s `resample()` bucketed by elapsed milliseconds from local midnight, which drifts off true wall-clock time on a 23- or 25-hour clock-change day; it now bins by the sample's local hour/minute instead.

**Verification.** `tests/test_config_entities_review_fixes.py`, `tests/test_flux_helpers_merge.py`, `tests/test_security.py`, `tests/test_settings_audit.py`.
