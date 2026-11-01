# Phase 42 — Import cause ledger ("why did I import?")

**Status:** Planned, not built. **Size:** M. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Assign every grid-import kWh each day to a cause: battery at its floor, load above what the inverter can discharge, battery held by a slot or target, grid charging in the window, a free event, or unknown. Publish the totals in a 22:00 record, a sensor and the weekly digest.

## Why (evidence)

Avoidable import was £4.99 this week and £10.37 the week before. On 31 Oct, 4.7 kWh came in during the morning window even though SOC was 95 at 05:00. The `over_limit_import_step` docstring (planning.py:660) says a flat SOC proves nothing, because a slot target can hold the battery while the house runs on the grid. Phase 40's `daytime_import_anomaly` (planning.py:1516) only alerts above a wattage threshold and never puts kWh against a cause.

## Design

Add `planning.import_cause_step(prev, sample, slot, free_window)` with tests, generalising `over_limit_import_step`. The samples carry `battery_power`, `grid_pac`, SOC and the Flux slot in force from `coordinator.state`. Accumulate per KPI segment through the existing SOC-change listener plus a 5-minute tick. At 22:00, write an `import_causes` record, add it to `_DEDUP_TYPES` and to the 22:00 bundle, and add `sensor.import_causes_today` with one attribute per cause. Put a cause breakdown in the weekly digest.

## Value

Diagnostic. If about 25–40% of the roughly £7.70/week of avoidable import proves controllable, it gives later phases a target of about £100–160/yr. Its first use is explaining incidents like 31 Oct.

## Risk

Sampling gaps or SOC plateaus can put kWh under the wrong cause. Keep an explicit 'unknown' bucket rather than guessing.

## Vetting note

Checked planning.py:660 and :1516. Nothing assigns import kWh to a cause. The evidence is concrete, it fits rule 19 (pure function in planning.py), and it feeds most later money decisions.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
