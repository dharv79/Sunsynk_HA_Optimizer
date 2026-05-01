# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (HACS) that optimises a Sunsynk inverter's charging and export behaviour using solar forecasts, battery SOC, and time-of-use tariff windows. It is pure Python with no build step — the `custom_components/sunsynk_optimizer/` directory is deployed directly into a running Home Assistant instance.

## Development workflow

There is no local test runner, linter config, or CI pipeline in this repo. Development requires a real or dev Home Assistant instance:

1. Copy `custom_components/sunsynk_optimizer/` into the HA instance's `custom_components/` directory.
2. Restart Home Assistant or reload the integration via **Settings → Devices & Services**.
3. Check **Settings → System → Logs** for errors from the `custom_components.sunsynk_optimizer` logger.

When iterating on logic, the manual trigger buttons (Run import plan, Run Flux 2 check, Run choose best day) are the primary way to test without waiting for scheduled events.

To validate Python syntax without a running HA instance:
```bash
python3 -m py_compile custom_components/sunsynk_optimizer/*.py
```

## Architecture

### Entry points and data flow

`__init__.py` → `SunsynkOptimizerCoordinator` → `SunsynkOptimizer`

- **`coordinator.py`** owns the `OptimizerState` dataclass (the single source of truth for all runtime state) and persists it via HA's `Store` helper at `.storage/sunsynk_optimizer_state_{entry_id}`. All state mutations go through `coordinator.update_state(**kwargs)`, which also triggers entity refreshes via `async_set_updated_data`.
- **`optimizer.py`** contains all business logic: import plan calculation, Flux 2 export/trim control, full-charge day scoring. It registers four HA listeners on startup (time-change at 01:55, time-change at 18:00 Sunday, 30-minute interval, battery SOC state change).
- **`api.py`** handles Sunsynk cloud API calls: RSA-encrypted login (fetches public key → encrypts password with PKCS1v15), bearer token management with automatic re-login on 401, and posting to the `/api/v1/plant/{plant_id}/income` endpoint.
- **`flux_helpers.py`** builds and mutates the `fluxProducts` payload. The two Flux windows are always index 0 (Flux 1, import, `direction=1`) and index 1 (Flux 2, export, `direction=0`). `apply_flux_override()` deep-copies and patches these; `build_payload()` assembles the full income POST body. `merge_entry_data()` is the canonical way to read config — it merges `entry.data` + `entry.options` with options winning, and fills defaults for `charges` and `fluxProducts` if absent.

### Config entry split

Credentials (`username`, `password`, `plant_id`, `inverter_serial`) live in `entry.data`. All other settings (`charges`, `flux_products`, thresholds, forecast entity, etc.) may live in either `entry.data` (initial setup) or `entry.options` (reconfiguration). Always call `merge_entry_data(dict(entry.data), dict(entry.options))` to read config — never read `entry.data` or `entry.options` directly in logic code.

### Key ID distinction

`plant_id` — numeric Sunsynk API plant/station ID used in all API calls.  
`inverter_serial` — alphanumeric serial used to build SolarSynkV3 sensor entity IDs like `sensor.solarsynkv3_{inverter_serial}_battery_soc`.  
These are different values and must never be swapped.

### Options flow

The options flow is multi-step: `init` → `charges_1` (import tariff rows 1–4) → `charges_2` (export tariff rows 5–8) → `flux` (baseline Flux windows). State is accumulated in `self._working` dict across steps before being saved on the final step.

### Entities

All entities extend `CoordinatorEntity` and read state from `coordinator.state` in their property methods. They receive updates only when the coordinator calls `async_set_updated_data`. No polling interval is set on the coordinator — updates are entirely event-driven.

- **Sensors** (`sensor.py`): expose `OptimizerState` fields; `import_plan_end` and `flux2_action` have rich `extra_state_attributes` exposing the full plan/action dicts.
- **Buttons** (`button.py`): call `optimizer` methods directly on press.
- **Binary sensors** (`binary_sensor.py`): `evening_export_disabled` and `monitor_only` (derived from `operation_mode == "monitor"`).

### Dashboard

`dashboard_installer.py` generates a Lovelace YAML file by building a Python dict and serialising it with `json.dumps`. All SolarSynkV3 entity IDs are constructed from `inverter_serial` via the local `s()` helper. The file is written to `{hass.config.config_dir}/sunsynk_optimizer_{entry_id}.yaml`, and a persistent HA notification shows the `configuration.yaml` snippet to add.

### Operation modes

`auto` — full optimizer behaviour, API writes enabled.  
`monitor` — all three main logic paths (`async_run_import_plan`, `async_run_flux2_check`, `async_choose_best_full_charge_day`) return early without making API calls.

### Scoring logic (full-charge day)

Scores Monday–Friday from weather forecast: base score `100 - cloud_coverage - (rain_prob * 0.7)`, adjusted by condition string (+25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow), temperature (±3), and day-of-week penalty (Thursday −5, Friday −15). Highest score wins.

### Import plan logic

Runs nightly at 01:55. SOC target is 100% on the selected full-charge day, otherwise scaled by `forecast_band` (summer_like ≥10 kWh → 80%, winter_like ≤5 kWh → 95–100%, shoulder → 85%). Import window end time (Flux 1) starts at 04:00 and is trimmed earlier based on current SOC and forecast band, clamped to 02:15–05:00. If forecast < 7 kWh the window is always extended to 05:00.
