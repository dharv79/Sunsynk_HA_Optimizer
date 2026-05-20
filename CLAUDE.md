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

- **`coordinator.py`** owns the `OptimizerState` dataclass (the single source of truth for all runtime state) and persists it via HA's `Store` helper at `.storage/sunsynk_optimizer_state_{entry_id}`. All state mutations go through `coordinator.update_state(**kwargs)`, which also triggers entity refreshes via `async_set_updated_data`. Storage saves are non-blocking (`hass.async_create_task`) via `_async_save_state()`, which catches errors and surfaces them in `last_error`.
- **`optimizer.py`** contains all business logic: import plan calculation, Flux 2 export/trim control, full-charge day scoring. It registers six HA listeners on startup (time-change at 01:55, 06:00, 18:00, and 22:00 daily; 30-minute interval; battery SOC state change). The 18:00 listener is gated to Sundays inside the handler. It also schedules a one-shot `async_call_later` 60 seconds after setup to run an initial import plan — this fires on every integration reload, not just first boot. The 60-second delay exists so that notify services are registered before the plan runs. `_essential_state(entity_id)` returns `float | None` and is used as a pre-flight check for battery SOC and forecast sensor availability before running plans. `_async_post_with_status(payload)` wraps all API pushes and returns a bool; callers gate notification text on it.
- **`data_logger.py`** records decisions and actuals to monthly JSONL files at `{config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl`. Four record types: `import_plan` (at 01:55), `morning_state` (at 06:00 — SOC and PV power before solar starts), `day_actuals` (at 22:00 — evening SOC and actual solar kWh), and `full_charge_day` (weekly scores). Per-day record types are deduplicated at write time (`_write_record` calls `_record_exists` before appending) to prevent double entries on HA restarts that cross a scheduled-event boundary. Provides four analysis methods used by `optimizer.py` to apply adaptive corrections: `compute_forecast_correction` (actual/forecast ratio over 30 days, capped 0.5–3.0, requires 7+ days), `compute_soc_target_adjustment` (±5% nudge based on evening SOC outcomes, requires 5+ matching non-high-solar days), `compute_overnight_drain_adjustment` (extra % to target SOC to compensate battery drain before 06:00, requires 5+ valid days), `compute_effective_charge_rate_kw` (kW from historical charge sessions, requires 3+ days). `count_soc_adjustment_days` counts all in-band days regardless of solar level (progress counter); the nudge computation still filters out high-solar days (`_HIGH_SOLAR_THRESHOLD_KWH = 15.0`). Files older than 13 months are pruned on startup via `coordinator.py`.
- **`api.py`** handles Sunsynk cloud API calls: RSA-encrypted login (fetches public key → encrypts password with PKCS1v15), bearer token management with automatic re-login on 401, and posting to the `/api/v1/plant/{plant_id}/income` endpoint. Uses the `cryptography` library (not declared in `manifest.json` because it is bundled with Home Assistant itself).
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

- **Sensors** (`sensor.py`): expose `OptimizerState` fields; `import_plan_end` and `flux2_action` have rich `extra_state_attributes` exposing the full plan/action dicts. Four dedicated adaptive learning sensors read from `last_import_plan`: `forecast_correction`, `overnight_drain_adjustment`, `evening_soc_adjustment`, `effective_charge_rate` — each exposes `days_collected`, `days_required`, and `active` in `extra_state_attributes`. Thresholds are defined in `_ADAPTIVE_THRESHOLDS`.
- **Buttons** (`button.py`): call `optimizer` methods directly on press. `run_import` and `run_flux2` pass `source="user_button"` so the action records are distinguishable from automatic runs.
- **Binary sensors** (`binary_sensor.py`): `evening_export_disabled` and `monitor_only` (derived from `operation_mode == "monitor"`).

### Dashboard

`dashboard_installer.py` generates a Lovelace YAML file by building a Python dict and serialising it with `json.dumps`. All SolarSynkV3 entity IDs are constructed from `inverter_serial` via the local `s()` helper. The file is written to `{hass.config.config_dir}/sunsynk_optimizer_{entry_id}.yaml` via `async_add_executor_job` (to avoid blocking the event loop), and a persistent HA notification shows the `configuration.yaml` snippet to add.

### Operation modes

`auto` — full optimizer behaviour, API writes enabled.  
`monitor` — all three main logic paths (`async_run_import_plan`, `async_run_flux2_check`, `async_choose_best_full_charge_day`) return early without making API calls.

### Scoring logic (full-charge day)

Scores Monday–Friday from weather forecast: base score `100 - cloud_coverage - (rain_prob * 0.7)`, adjusted by condition string (+25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow), temperature (±3), and day-of-week penalty (Thursday −5, Friday −15). Highest score wins.

### Import plan logic

Runs nightly at 01:55. Begins with a pre-flight check: battery SOC and forecast sensor must be available. If either is unavailable the plan is skipped and `last_error` is set. The forecast sensor has a fallback: if unavailable but a prior `raw_forecast_kwh` exists in `last_import_plan`, it is reused (with no correction factor applied).

Before calculating targets, four adaptive corrections are fetched from `data_logger.py` (each returns a neutral value until enough history exists):

1. **Forecast correction factor** — raw forecast kWh is multiplied by `actual/forecast` ratio from the last 30 days.
2. **Overnight drain adjustment** — extra % added to `target_soc` to compensate for battery drain between charge end and 06:00.
3. **Evening SOC adjustment** — ±5% nudge to `target_soc` based on whether the battery has been ending the day too full or too empty (high-solar days excluded from the nudge direction but counted toward the progress threshold).
4. **Effective charge rate** — calibrated kW rate from historical charging sessions, used to size the Flux 1 window precisely.

**SOC target selection:**
- **Full charge day** (selected weekly best day): if forecast ≥ 7 kWh and `sun.sun` is available, uses solar bridge target (same as regular days) so solar charges the battery to 100% during the day for free. Falls back to grid-to-100% if forecast is poor.
- **Low solar (< 7 kWh)**: winter_like → 100%, other bands → 95%.
- **Solar bridge** (normal path when `sun.sun` available): `target = 20 + (hours_to_solar × avg_consumption / battery_capacity) × 100`, clamped 30–100%.
- **Band fallback** (no `sun.sun`): summer_like → 80%, shoulder → 85%, winter_like → 95%.

Drain and evening-nudge adjustments are applied whenever `target_soc < 100` (covers both regular nights and full-charge-day solar bridge plans). Skipped when target is already 100%.

Import window (Flux 1) is physics-based: `minutes = (energy_needed_kwh / used_charge_rate) × 60`, rounded up to the next 15-minute slot, clamped 02:15–05:00. Extended to 05:00 when forecast < 7 kWh.

All API pushes go through `_async_post_with_status()` which returns a bool. Notifications are prefixed with "⚠ API push FAILED" if the push did not succeed. The plan state dict always includes `api_ok`, `source`, and `forecast_fallback` fields.
