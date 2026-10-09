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

The options flow is multi-step: `init` → `charges_1` (import tariff rows 1–4) → `charges_2` (export tariff rows 5–8) → `flux` (baseline Flux windows). State is accumulated in `self._working` dict across steps before being saved on the final step.

### Operation modes

`auto` — full optimizer behaviour, API writes enabled.  
`monitor` — all three main logic paths (`async_run_import_plan`, `async_run_flux2_check`, `async_choose_best_full_charge_day`) return early without making API calls.


## Shadow-first options

- `cost_aware_export_shadow_mode` (default on, phase 3): see export-control.md.
- `import_feedback_live` (default off, phase 15): when off, the day-rate import feedback is only logged; when on, it replaces the evening SOC nudge. See import-plan.md.
- `peak_export_live` (default off, phase 17): when off, the 16:00 peak surplus export decision is only logged; when on, Flux 2 exports 16:00–19:00 down to the evening reserve + 5%. See export-control.md.

## Octopus rate entities (phase 18)

`octopus_import_rates_entity` / `octopus_export_rates_entity` (optional, free text): an Octopus Energy current-rate sensor or day-rates event. Blank keeps pricing on `charges`. See export-control.md.

## Saving sessions (phase 21)

`saving_session_reward_pence` (optional, p/kWh) prices manual sessions; blank = unknown reward (the plan keeps the evening reserve and doesn't top up). `octopus_saving_session_entity` (optional, free text): the Octopus Energy `event.…_octoplus_saving_session_events` or `event.…_octoplus_power_down_events` entity; blank disables auto-detect. Charge/export rates reuse `free_event_charge_rate_kw` / `free_event_export_rate_kw`. See free-electricity.md.
