# Phase 85 — Prorated bill reconciliation and missing-days alert

**Status:** Done (10/11/2026). **Size:** XS-S, ~20-30k est. **Origin:** idea sweep 08/11/2026 (ideas 15, 16); follow-up to phase 36 and the 07/11/2026 `#sunsynkdebug` review.

## Goal

Make the reconciliation meaningful when days are missing. The 8 Sep-7 Oct run logged £39.39 over 19 of 30 days against a £65.02 bill, a 39% gap that is mostly the 11 missing days, not an error in the cost model.

## Vetting (08/11/2026)

- `planning.py` already returns `days_logged`, `days_in_month` and `partial`; phase 45's audit already raises a Repairs issue for a missing `daily_cost` (only after a 2-day settle).
- Not done: a per-day comparison (logged £/day against billed £/30 days), and a threshold alert for low coverage.
- Backfilling missing days needs the Octopus consumption API, which requires a key. The integration must not require one, so backfill stays limited to what the existing Octopus sensors report (rule 12, fill-only).

## Design

- Add `gap_per_day_pct` and a `projected_logged_gbp` (logged / days_logged × days_in_month) to the reconciliation record. Alert on the projected gap, not the raw gap. Missing → `None`.
- New `coverage_low` flag when under 25 of 30 days are logged; one Repairs issue, auto-cleared.
- Pure functions in `planning.py` with tests (do-not-break 19).

## Savings estimate

No direct £ saving. It stops a false "model is 39% wrong" signal and shows the true cost-model error, which gates the tariff and scorecard decisions.
