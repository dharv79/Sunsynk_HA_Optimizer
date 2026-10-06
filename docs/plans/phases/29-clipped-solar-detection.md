# Phase 29 — Clipped solar detection

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 2, 06/10/2026 (item 30).

## Goal

Measure solar lost on days the battery is full and export is at the inverter's limit — evidence the overnight target or forecast was too high.

## Design

- Option: export limit (W); blank → feature off.
- 30-min tick: when SOC ≥ 99 and export ≥ 95% of the limit, accumulate clipped-time; estimate lost kWh from forecast minus actual over those periods (pure `planning.estimate_clipped_kwh`).
- Log `clipped_solar_kwh` in `day_actuals`; when the battery was full before 11:00 on a clipped day, flag `overnight_target_too_high` for phases 15/20 to consume.
- Weekly digest line: "~3.2 kWh solar clipped this week".

## Acceptance

- Tests for the estimator (no clipping, partial, missing sensors → `None`).
- No inverter writes.
