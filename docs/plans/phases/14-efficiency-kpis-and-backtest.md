# Phase 14 — Efficiency KPIs and backtest harness

**Status:** Done, merged (166f29f, PR #38). **Size:** M, ~45k actual.

**As built:**

- `meter_snapshot` records are logged at 02:00, 05:00, 16:00 and 19:00 by listeners, and at 22:00 inside the day-actuals capture. They hold the cumulative grid import/export, load and SOC, with dedup per date and time.
- `planning.day_kpis` covers 00:00–22:00, because the meters reset at midnight. It computes the off-peak / day / peak import split, self-sufficiency, avoidable £, peak export kWh and £, and unused overnight charge. Each KPI is `None` on any missing input.
- The KPIs are logged as `day_kpis`, added to the 22:00 bundle, paired into paired days, and rolled up by `planning.week_kpis` in the digest.
- `tools/backtest.py` replays plan and morning records with `--target-offset` / `--load-kw`, using an approximate 06:00-reserve shortfall model. It also reports live vs weighted forecast MAE. `hours_to_solar` was added to `_IMPORT_PLAN_FIELDS` for it.
- The baseline week starts once the snapshots log (first full day after install).

Details: `docs/architecture/data-logging.md`. **Origin:** improvement list 06/10/2026 (item 10).

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
