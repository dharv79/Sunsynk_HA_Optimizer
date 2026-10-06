# Phase 12 — Learned household load

**Status:** Planned, not built. **Size:** S, ~25-45k est. **Origin:** improvement list 06/10/2026 (item 1).

## Goal

Replace the static `avg_consumption_kw` (default 0.75 kW) in the solar bridge (`bridge_soc`, `walk_bridge_gap`) with a load rate learned from logged history, so the overnight charge matches what the house actually uses. Too high buys unneeded off-peak kWh; too low runs short and buys at the day rate.

## Prerequisite

Fix/guard the zeroed 28 Sep `day_actuals` record: meters unavailable at 22:00 currently log `0.0` load (defaults in `async_log_day_actuals`). Missing meter → `None`, and learning ignores `None`/zero-load days, or the learned rate is dragged down.

## Design

- Pure `planning.learned_load_kw(days, weekday, ...)`: median overnight load rate from `overnight_load_kwh` over the hours it covers, last ~28 home days, optionally split weekday/weekend; returns `None` below a minimum day count (e.g. 7).
- Excluded: away days (do-not-break 18), full-charge days, days with missing/zero load.
- Used rate = learned if available, else config; clamp to a sane band (e.g. 0.5×–2× config) as a guard.
- Log `avg_consumption_kw_used` and `load_source` (`learned`/`config`) on the import plan (add to `_IMPORT_PLAN_FIELDS` if paired — do-not-break 10).

## Acceptance

- Unit tests: below threshold → None, away/zero days excluded, weekday split, clamp.
- 22:00 bundle plan line shows the learned rate and source.
- Docs: `import-plan.md` feature section, `testing.md` row.
