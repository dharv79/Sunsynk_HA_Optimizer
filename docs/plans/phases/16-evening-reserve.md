# Phase 16 — Evening reserve to 02:00

**Status:** Done, merged (f739100, PR #40). **Size:** S, ~20k actual.

**As built:**

- `planning.evening_reserve_soc(load_kw, capacity)` is `min(100, ceil(20 + load × 7 h / capacity × 100))`. The load is the plan rate, or config.
- `planning.trim_target_soc` returns `max(82, reserve)`, or `None` (skip) when that is not below the SOC.
- Both trims use it. The action names are kept, and the sensor reads the real target from the payload.
- At the defaults the reserve is 73%, so there is no behaviour change until the load or capacity pushes it above 82%.
- The 22:00 `day_kpis` line carries `evening_reserve_soc` and a new `grid_import_evening_kwh` (19:00–22:00 only, because the meters reset at midnight).

Details: `docs/architecture/export-control.md`. **Origin:** improvement list 06/10/2026 (item 3).

## Goal

Ensure the battery covers house load from 19:00 to the 02:00 off-peak start, so evenings don't buy at the day rate after the peak export.

## Design

- Pure `planning.evening_reserve_soc(load_kw, hours=7, capacity, floor)`; load from phase 12.
- Every sell-down (daytime trim at 82%, full-charge-day trim, phase 17 peak export) uses `max(trim_target, evening_reserve_soc)` as its floor when it would leave the battery below the reserve by 19:00.
- Log `evening_reserve_soc` in the 22:00 bundle; KPI (phase 14) shows 19:00–02:00 import.

## Acceptance

- Tests for the reserve maths and the `max` floor.
- Do-not-break 7 (reload one-shot / free-event slots) untouched.
