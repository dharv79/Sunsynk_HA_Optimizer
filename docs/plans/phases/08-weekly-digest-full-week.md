# Phase 8 — Weekly digest covers a full 7 days

**Status:** Planned, not built. **Size:** XS, ~15-25k.

## Scope

Every weekly cost summary reports `days_in_period: 6`, not 7.

## Diagnosis (from code, unverified in production)

`_async_send_weekly_cost_summary` runs Sunday 18:00 and calls `async_load_paired_days(days=7)`, which filters records by `recorded_at >= now − 7 days`. A paired day needs both `import_plan` (01:55) and `day_actuals` (22:00):
- Sunday's own `day_actuals` isn't written until 22:00 → today is unpaired.
- Last Sunday's `import_plan` (01:55, ~7 d 16 h ago) falls outside the cutoff → last Sunday is unpaired.

So the digest covers Mon–Sat. `_async_send_ai_weekly_insight` uses the same load.

## Design options

- Load `days=8` and filter paired days to the 7 dates ending yesterday (Sun–Sat), explicitly by `date`. Preferred: a deterministic, labelled period.
- Add `period_start`/`period_end` to the digest so the window is self-describing.

## Acceptance criteria

- Digest reports 7 days when all records exist, with explicit period dates.
- Pure date-window helper unit-tested (row added to docs/architecture/testing.md).
- Year-to-date figure unchanged.
