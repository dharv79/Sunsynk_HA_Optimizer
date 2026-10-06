# Phase 14 — Efficiency KPIs and backtest harness

**Status:** Planned, not built. **Size:** M, ~50-80k est. **Origin:** improvement list 06/10/2026 (item 10).

## Goal

Measure whether phases 12–20 actually reduce purchased energy, and tune them offline before going live.

## Design

- **KPIs (weekly digest + 22:00 bundle):** self-sufficiency %, grid import kWh split off-peak / day / peak, avoidable day-rate import £ (day + peak import × price), unused overnight charge (SOC at 16:00 above evening need — needs a 16:00 SOC snapshot), export at peak kWh and £.
- Needs time-banded meter snapshots: log cumulative grid import/export at 02:00, 05:00, 16:00, 19:00, 22:00 (new `meter_snapshot` record, dedup per date+time).
- **Backtest:** HA-free script (`tools/backtest.py`) that replays logged `import_plan`/`day_actuals`/`morning_state` through `planning.py` with alternative parameters and reports estimated grid import / cost deltas. Read-only on the data dir.

## Acceptance

- Snapshot capture + KPI maths unit-tested; missing snapshot → KPI `None`, never 0.
- Backtest runs against a fixture data dir in tests.
- One week of baseline KPIs captured before phases 15–17 go live.
