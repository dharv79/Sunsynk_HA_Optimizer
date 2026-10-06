# Phase 16 — Evening reserve to 02:00

**Status:** Planned, not built. **Size:** S, ~25-45k est. **Origin:** improvement list 06/10/2026 (item 3).

## Goal

Ensure the battery covers house load from 19:00 to the 02:00 off-peak start, so evenings don't buy at the day rate after the peak export.

## Design

- Pure `planning.evening_reserve_soc(load_kw, hours=7, capacity, floor)`; load from phase 12.
- Every sell-down (daytime trim at 82%, full-charge-day trim, phase 17 peak export) uses `max(trim_target, evening_reserve_soc)` as its floor when it would leave the battery below the reserve by 19:00.
- Log `evening_reserve_soc` in the 22:00 bundle; KPI (phase 14) shows 19:00–02:00 import.

## Acceptance

- Tests for the reserve maths and the `max` floor.
- Do-not-break 7 (reload one-shot / free-event slots) untouched.
