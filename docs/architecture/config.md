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
