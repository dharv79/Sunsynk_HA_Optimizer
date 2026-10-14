# Phase 39 — Multi-day low-solar planning

**Status:** Planned, not built. **Size:** M, ~45-70k est. **Origin:** improvement list 3, 14/10/2026 (item 9).

## Goal

In a run of dull days, plan across days instead of one at a time: fill on cheap nights and don't sell what tomorrow will have to buy back.

## Design

- Read the 2-3 day forecast (Forecast.Solar/Solcast attributes or the weather entity's daily forecast as a proxy).
- Pure `planning.low_solar_run(days_kwh, threshold)` → length of the low run starting today.
- While in a run of ≥2 days: 01:55 target 100%; 16:00 peak export only sells above tomorrow's need (reserve + tomorrow's day load minus forecast solar); daytime trims off.
- Shadow first: log what it would have changed (`multi_day_plan` record) before a live option.

## Acceptance

- Tests: run detection, need maths, single low day → unchanged behaviour, missing forecast → unchanged.
