# Phase 120 — Payback tracker

**Status:** Done, merged (f241c06, PR #235). **Size:** S, ~25k. **Origin:** user request, 17/11/2026 (follows phase 118).

## Goal

Show on the dashboard how much of the £9000 investment (solar and battery, since June 2025) the savings have repaid.

## Design

- `planning/savings.py`: `payback_summary(ledger, opening_gbp, today)` gives saved (opening balance + ledger total), remaining, % repaid, the last-90-day £/day and a projected payback date. The date is `None` with fewer than 7 recent days or a saving of 0 or less (never guessed).
- `number.saved_before_tracking` (state `payback_opening_gbp`, default 0): the user's own figure for June 2025 to 9 Oct 2026, when the ledger starts. Nothing is invented.
- `sensor.payback` (% repaid, attributes from the summary), a gauge and a "Payback since June 2025" card in the Daily KPIs section.
- Investment £9000 and start 2025-06-01 are constants (`PAYBACK_INVESTMENT_GBP`, `PAYBACK_START`).

## Limits

The ledger only starts 10 Oct 2026, so the percentage is only as good as the opening balance entered. Winter-heavy data makes the 90-day projection pessimistic. Read-only.
