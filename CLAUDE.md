# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (HACS) that optimises a Sunsynk inverter's charging and export behaviour using solar forecasts, battery SOC, and time-of-use tariff windows. It is pure Python with no build step — the `custom_components/sunsynk_optimizer/` directory is deployed directly into a running Home Assistant instance.

## Development workflow

### Tests

The pure adaptive-learning logic in `data_logger.py` has unit tests under `tests/` that run **without** a Home Assistant install — `tests/conftest.py` stubs `homeassistant.core` and loads `data_logger.py` directly by file path (bypassing the package `__init__.py` and its coordinator/optimizer import chain). Run them with:

```bash
pip install pytest && python3 -m pytest
```

`tests/conftest.py` exposes a `dl` fixture (a bare `DataLogger` instance for calling its pure methods) and a `make_day(**overrides)` helper for building paired-day dicts. When you change any `compute_*` / `count_*` / `_is_drain_night` / `_percentile` logic, add or update a test that pins the new behaviour. The `.github/workflows/tests.yml` workflow runs `py_compile` + `pytest` on every push to `main` and every PR. Testing code that imports HA more deeply (optimizer, coordinator) requires mocking `hass`; prefer extracting pure helpers so they can be tested directly.

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

## Architecture

### Entry points and data flow

`__init__.py` → `SunsynkOptimizerCoordinator` → `SunsynkOptimizer`

- **`coordinator.py`** owns the `OptimizerState` dataclass (the single source of truth for all runtime state) and persists it via HA's `Store` helper at `.storage/sunsynk_optimizer_state_{entry_id}`. All state mutations go through `coordinator.update_state(**kwargs)`, which also triggers entity refreshes via `async_set_updated_data`. Storage saves are non-blocking (`hass.async_create_task`) via `_async_save_state()`, which catches errors and surfaces them in `last_error`.
- **`optimizer.py`** contains all business logic: import plan calculation, Flux 2 export/trim control, full-charge day scoring. It registers six HA listeners on startup (time-change at 01:55, 06:00, 18:00, and 22:00 daily; 30-minute interval; battery SOC state change). The 18:00 listener is gated to Sundays inside the handler. It also schedules a one-shot `async_call_later` 60 seconds after setup to run an initial import plan — this fires on every integration reload, not just first boot (the 60-second delay lets notify services register first). That one-shot is **skipped** if a reload lands between 16:00–19:00 while `evening_export_disabled` is set, so it can't re-push Flux 2 and re-enable export mid-pause. The scheduled callbacks (`_async_run_import_plan`, `_async_periodic_flux2_check`, `_async_choose_best_full_charge_day`) run through the `_guarded` wrapper, which catches exceptions and surfaces them in `last_error` rather than letting them escape into the event loop (a silent nightly failure). `_essential_state(entity_id)` returns `float | None` and is used as a pre-flight check for battery SOC and forecast sensor availability before running plans. `_async_post_with_status(payload)` wraps all API pushes and returns a bool; callers gate notification text on it.
- **`data_logger.py`** records decisions and actuals to monthly JSONL files at `{config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl`. Four record types: `import_plan` (at 01:55), `morning_state` (at 06:00 — SOC and PV power before solar starts), `day_actuals` (at 22:00 — evening SOC and actual solar kWh), and `full_charge_day` (weekly scores). Per-day record types are deduplicated at write time (`_write_record` calls `_record_exists` before appending) to prevent double entries on HA restarts that cross a scheduled-event boundary. Provides four analysis methods used by `optimizer.py` to apply adaptive corrections:
  - `compute_forecast_correction` — **median** (not mean) of actual/forecast ratios over 30 days, capped 0.5–3.0, requires 7+ days. Median so one anomalous day (tiny forecast, huge actual → unbounded ratio) can't jolt the factor. The denominator is the stored *corrected* forecast, which makes the update self-damping: at equilibrium the factor settles at sqrt(true raw bias) — deliberate under-correction, the safe direction (planner expects less solar than arrives → charges more).
  - `compute_soc_target_adjustment` — ±5% nudge based on evening SOC outcomes, requires 5+ matching non-high-solar days.
  - `compute_overnight_drain_adjustment` — p75 (`_DRAIN_PERCENTILE`) of overnight drain, extra % to target SOC to compensate battery drain before 06:00, requires 5+ valid days, 15% fallback below that. Qualifying nights are defined by the shared `_is_drain_night` predicate, which requires a **real overnight charge** (`initial_soc < target_soc`) — no-charge nights (battery already above target, just discharging from a high start) are excluded so they can't be mistaken for post-charge drain.
  - `compute_effective_charge_rate_kw` — kW from historical charge sessions (needs a night with `target − initial ≥ 10%`), requires 3+ days, else `None`. When it returns `None`, `optimizer.py` reuses the last learned rate instead of the nameplate config: fallback chain is fresh computation → persisted `OptimizerState.last_effective_charge_rate_kw` → `last_known_charge_rate_kw(paired_days)` (most recent non-null rate in history, seeds the persisted value on first run). Summer high-SOC nights rarely reach the 10% gap, so this fallback is the normal path much of the year.

  `count_*` progress counters mirror their compute predicates (drain uses the same `_is_drain_night`), except `count_soc_adjustment_days`, which intentionally counts all in-band days regardless of solar level while the nudge computation still filters out high-solar days (`_HIGH_SOLAR_THRESHOLD_KWH = 15.0`). Files older than 13 months are pruned on startup via `coordinator.py`.

  **Home/away calibration split.** Each plan runs in an occupancy regime — `away = coordinator.state.away_mode` (toggled by the built-in **Away mode** switch, `switch.py`, persisted in `OptimizerState`). The drain (`compute_overnight_drain_adjustment` / `_is_drain_night`) and evening-nudge (`compute_soc_target_adjustment`) computations and their counters take an `away` parameter and filter to days whose logged `away` flag matches, so a low-load holiday learns its own profile and can't skew the home one (and vice versa). Charge-rate calibration excludes away days entirely (the rate is physical; away nights back-calculate through an atypical drain), so when away it reuses the home-learned rate via the normal fallback chain. Forecast correction stays global (load-independent). Each `import_plan` record is tagged with `away`; `_pair_records` carries it into the paired dict (default `False`, so all pre-1.0.9 history reads as home).
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
- **Buttons** (`button.py`): call `optimizer` methods directly on press. `test_plan` calls `async_run_import_plan(source="test_button", dry_run=True)` — recomputes the full plan and notifies the JSON, but does not push to the inverter, log, or mutate state. Also `choose_best_day`, `reset_baseline`, `install_dashboard` (labelled "Update dashboard"). The old `run_import`/`run_flux2` push buttons were removed — pressing them mid-day pushed a daytime SOC reading to the inverter; the dry-run test replaces them. The `async_run_import_plan` / `async_run_flux2_check` methods still exist for the scheduled listeners.
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

1. **Forecast correction factor** — raw forecast kWh × the median actual/forecast ratio from the last 30 days (see `compute_forecast_correction` above).
2. **Overnight drain adjustment** — p75 extra % added to `target_soc` to compensate for battery drain between charge end and 06:00, measured only on real-charge nights.
3. **Evening SOC adjustment** — ±5% nudge to `target_soc` based on whether the battery has been ending the day too full or too empty (high-solar days excluded from the nudge direction but counted toward the progress threshold).
4. **Effective charge rate** — calibrated (or last-known, see above) kW rate used to size the Flux 1 window precisely.

**`low_solar_forecast_kwh` = min(raw, corrected).** All low-solar decisions (the `< 7 kWh` target override, the full-charge-day bridge-vs-grid choice, and the extend-window-to-05:00 rule) key on this pessimistic value, not the corrected forecast. The learned uplift is derived mostly from good days; on a genuinely bad day the raw forecast is already right, and multiplying it above 7 kWh would skip the max-import override and leave the battery short. If either forecast says a bad day, believe it.

**SOC target selection:**
- **Full charge day** (selected weekly best day): if `low_solar_forecast_kwh ≥ 7` and `sun.sun` is available, uses solar bridge target (same as regular days) so solar charges the battery to 100% during the day for free. Falls back to grid-to-100% if forecast is poor.
- **Low solar (`low_solar_forecast_kwh < 7`)**: winter_like → 100%, other bands → 95%.
- **Solar bridge** (normal path when `sun.sun` available): `target = 20 + (hours_to_solar × avg_consumption / battery_capacity) × 100`, clamped 30–100%.
- **Band fallback** (no `sun.sun`): summer_like → 80%, shoulder → 85%, winter_like → 95%.

Drain and evening-nudge adjustments are applied whenever `target_soc < 100` (covers both regular nights and full-charge-day solar bridge plans). Skipped when target is already 100%.

Import window (Flux 1) is physics-based: `minutes = (energy_needed_kwh / used_charge_rate) × 60`, rounded up to the next 15-minute slot, clamped 02:15–05:00. Extended to 05:00 when `low_solar_forecast_kwh < 7`. `used_charge_rate = min(config charge rate, effective_charge_rate)`, then multiplied by a battery-temperature deration factor.

All API pushes go through `_async_post_with_status()` which returns a bool. On failure the notification title switches to a "⚠️ Sunsynk: … NOT applied" variant. On a successful push the import plan clears any stale `last_error`. The plan state dict always includes `api_ok`, `source`, `forecast_fallback`, `low_solar_forecast_kwh`, and `charge_rate_from_cache` fields. Notification titles follow a `🔋 Sunsynk: <sentence case>` convention.
