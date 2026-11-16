# Phase 118 — Savings history and running total

**Status:** Done, merged (71fb4cf, PR #230). **Size:** S, ~30k. **Origin:** user request, 16/11/2026 (follows phase 117).

## Goal

Keep a history of the daily saving from solar and battery, back-dated as far as the logs go, with an all-time total.

## Design

- `planning/savings.py`: ledger `{date: [no_system_gbp, actual_gbp, "day"|"est"]}` in coordinator state (`savings_ledger`, `savings_total`, `savings_backfilled`). It is not pruned with the 13-month logs, so the total keeps growing.
- Live: the 22:00 `day_kpis` run adds the day, priced at that day's rates (`day`). A `day` entry is never replaced by an estimate.
- Back-date: once, 150 s after the first start on this version, from every stored meter-snapshot day (`BACKFILL_DAYS` 400) at today's tariff rates (`est`, because past rates are not logged). Skips today, free-event days and days with an incomplete snapshot set or missing price. One notification and a `savings_backfill` debug line. Read-only.
- Shown: `sensor.savings_total` (all-time £, attributes `since`, `days`, `estimated_days`, `by_month`), a "Savings by month" dashboard card, and the running total in the Sunday notification.

## Limits

Estimated days use today's Flux rates, so they can be off where the rates have changed since. They are counted in `estimated_days`. Only days with logged meter snapshots can be back-dated.
