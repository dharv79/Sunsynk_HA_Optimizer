# Phase 15 — Day-rate import feedback for the overnight target

**Status:** Done, merged (18667e7, PR #39). **Size:** S, ~25k actual.

**As built:** `planning.import_feedback_adjustment(paired_days, today, band, away)` uses the last 14 days in the same band and regime, with at least 5 days needed. It excludes full-charge days, export-disabled days, 100%-target nights and days without KPIs. A median 05:00–16:00 import above 0.3 kWh gives +5. Otherwise a median `unused_charge_kwh` above 0.5 kWh gives −5 (this replaces the 'evening SOC high' test, because unused charge is the direct signal). Otherwise the result is 0. `day_kpis` gained `grid_import_morning_kwh`. The result is logged in shadow as `import_feedback_*`. The new option `import_feedback_live` (default off) makes it replace the evening SOC nudge. Details: `docs/architecture/import-plan.md`. **Origin:** improvement list 06/10/2026 (item 2).

## Goal

Replace/augment the evening SOC nudge (fixed 35%/20% thresholds, flagged stale in `docs/logic.md` §8) with the real cost signal: grid import outside the off-peak window.

## Design

- Depends on phase 14 meter snapshots (import 05:00–16:00 and 16:00–02:00).
- Pure `planning.import_feedback_adjustment(days, band)`: per forecast band, if recent days show day-rate import above a small tolerance (e.g. 0.3 kWh), +5%; if none and evening SOC is high, −5%; neutral below a minimum day count. Same exclusions as the evening nudge (full-charge days, export-disabled days, away days).
- Ship in shadow first: log `import_feedback_adjustment` beside the existing nudge; switch on via a config flag once the backtest agrees.

## Acceptance

- Tests: import → raise, no import + high SOC → lower, below threshold → 0, exclusions.
- Shadow value visible in the 22:00 plan line.
