# Phase 94 — Stable entity IDs and long-term statistics

**Status:** Done, merged (c62f699, PR #202). **Size:** S-M, ~35-50k est. **Origin:** code review 11/11/2026. Starts with a feasibility check on a fresh HA install.

## Goal

Make the generated dashboard work on a new install, and keep history for slow-moving sensors.

## Findings

- Every entity has `has_entity_name = True` and, since phase 47, a device named "Sunsynk Optimizer". HA then creates `<domain>.sunsynk_optimizer_<name>` unless the entity ID is pinned, and only the newer entities are pinned. 22 IDs that the dashboard references are unpinned (for example `sensor.current_soc_target`, `sensor.next_import_window`, `binary_sensor.evening_export_disabled`, `button.test_plan`, `switch.away_mode`). Existing installs keep working because the registry holds the old IDs. Not tested on a live fresh install: confirm first.
- `SunsynkOptimizerSensor` sets no `state_class`; `current_soc_target` and the adaptive sensors have no unit. So `sensor.battery_soh` and the capacity estimate lose history after the recorder purge, and the "SOC vs target" graph draws the target as a state bar.

## Design

- Feasibility: install on a clean HA and list the generated IDs. If they match the dashboard, close the first finding with no change.
- Otherwise pin the 22 IDs, and have `async_install_dashboard` resolve IDs from the entity registry by unique_id so renamed entities also work. Test: every optimizer ID in the rendered dashboard is pinned.
- Add `%` to `current_soc_target` and `SensorStateClass.MEASUREMENT` to the numeric sensors. No `device_class=ENERGY` on daily kWh values.

## Saving

None. New-install correctness and history that survives the recorder purge.

## As built (12/11/2026)

The fresh-install check could not be run in the build environment, so both parts of the fix were built: every entity ID is pinned, and the dashboard installer maps IDs through the entity registry. An install made between phase 47 and this fix needs one press of **Update dashboard**. Details: `docs/architecture/entities-dashboard.md`.
