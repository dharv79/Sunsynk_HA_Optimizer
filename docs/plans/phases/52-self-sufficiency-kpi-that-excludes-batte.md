# Phase 52 — Self-sufficiency KPI that excludes battery grid-charging and free import

**Status:** Planned, not built. **Size:** XS. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Calculate self_sufficiency_pct as load not met by paid import for house use. Take out free-event import (free_credit, already passed in) and the 02:00-05:00 battery gain (soc05 − soc02 × capacity) before dividing. Keep the old ratio as grid_independence_pct.

## Why (evidence)

planning.day_kpis line 725 is `1 - imp22/load22`. On a Flux night, the off-peak charge plus any free refill can exceed the day's load, which clamps the KPI to 0%. That happened on 25 Oct with 13.7 kWh of solar, and 31 Oct read 6.9%. The function already receives free_credit but uses it only for the band split, not here. The figure feeds the weekly digest mean (planning.py:816) and the AI insight.

## Design

In planning.day_kpis, subtract sum(free_credit import) and max(0, soc05 − soc02)/100 × capacity from imp22, then clamp to 0-100. Return None when soc02 or soc05 is missing; never return 0. Add grid_independence_pct (the old formula) to DAY_KPI_FIELDS and to the weekly mean. Add a kpi_version field to day_kpis records. Test in tests/test_kpis.py with the 25 Oct numbers, a plain winter day and a free-event day.

## Value

Reporting only. The headline KPI becomes meaningful on every grid-charge day: about 40-60% instead of 0-14%. That stops the AI insight and digest drawing wrong conclusions.

## Risk

The historic series shifts. Show both fields and the version tag for a few weeks.

## Vetting note

Confirmed. Line 725 uses raw imp22, and free_credit is unused for this KPI. This merges two duplicate candidates.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
