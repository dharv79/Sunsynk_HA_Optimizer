# Phase 40 — Daytime load spike alert

**Status:** Planned, not built. **Size:** XS–S, ~20-30k est. **Origin:** improvement list 3, 14/10/2026 (item 10).

## Goal

Spot day-rate grid import while the battery should be covering the house (inverter limit, wrong priority, a heater left on).

## Design

- On the 30-min tick 05:00-16:00: if grid import (grid_pac) stays above a threshold (default 1.5 kW) for 2 consecutive checks while SOC is above `BATTERY_LOW_SOC` + 5 and no event or peak cover holds the slots, alert once per day.
- Pure `planning.daytime_import_anomaly(samples, soc, threshold)`.
- Read-only; runs in monitor mode; away mode lowers the threshold.

## Acceptance

- Tests: sustained vs single spike, low SOC (expected import) → no alert, missing sensors → no alert.
