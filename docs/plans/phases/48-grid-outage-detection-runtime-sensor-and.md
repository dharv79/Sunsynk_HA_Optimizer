# Phase 48 — Grid outage detection, runtime sensor and outage log

**Status:** Done (04/11/2026). **Size:** S. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Watch SolarSynkV3 `grid_status`. Notify when an outage starts and when the grid is restored. Show a 'battery runtime left' sensor (SOC above shutdown × capacity ÷ live load), suppress plan, trim, export and retry pushes during the outage, and log an `outage` record (start, end, SOC low point, kWh served).

## Why (evidence)

`grid_status` is wired only into the dashboard (dashboard_installer.py:232) and is read by no decision code. During an outage, today's `_async_post_with_status` failures would send '⚠️ … NOT applied' alerts and phase 32 retries that cannot work. The 22:00 SOC sat on the 25% floor on 4 of 7 days, so the household has no view of how much backup it has.

## Design

Add a `grid_status_entity` property next to the other `_solarsynk_entity` ones (optimizer.py:~381). Add a state listener that sets `coordinator.update_state(grid_outage={...})`. Add `planning.outage_runtime_hours(soc, shutdown_soc, capacity, load_kw)` with tests. Add early returns in `async_run_import_plan`, the Flux 2 check, peak cover and the plan retry while an outage is active. Add `binary_sensor.grid_outage` and `sensor.battery_runtime`. Log the `outage` record without dedup, and add a monthly summary line.

## Value

Mainly safety and peace of mind: it tells the household how long the battery will last. It stops about 5 false failure alerts per outage, and builds an outage history that can later justify a reserve SOC.

## Risk

The `grid_status` value format (string or numeric) needs a live sample, and the feature degrades to off if the entity is missing. A test or simulated outage is the only way to verify it.

## Vetting note

Merged the outage-logger half of 'Grid outage logger and storm reserve' into this candidate. The storm-reserve half goes to the runners-up. Confirmed that grid_status appears only in dashboard_installer.py:232. The feature is read-only plus push suppression, so it is safe.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (04/11/2026)

`planning.grid_online` (tolerant parser), `outage_runtime_hours`, `outage_track`, `outage_record`. A `grid_status` listener opens and closes an outage (persisted `grid_outage`), notifies, and logs an `outage` record. While active, the import plan, Flux 2 check, peak cover, peak export and plan retry return early; a skipped plan re-runs on restore. `binary_sensor.grid_outage`, `sensor.battery_runtime`. Confirm the live `grid_status` format; an unrecognised value is never treated as an outage.
