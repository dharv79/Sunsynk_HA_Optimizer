# Phase 134: Daily health ignores a small taper shortfall

Status: Done, merged (03eb8643d123b2a9b54f457ec0922e0a134cef37, PR #257)

## Why

From 16/11/2026 nearly every night the overnight charge ended 5–6% below a 100% target because the battery tapers near full in the cold. `daily_health` turned that into a nightly "Check", hiding real faults.

## Decisions

User decision (25/11/2026): a taper shortfall of 6% or less is not a health Check. Other causes, and bigger gaps, still are.

## What

- `planning.charge_shortfall_flagged`, `HEALTH_TAPER_TOLERANCE_PCT = 6.0`.
- `daily_health(..., charge_shortfall_cause)`; the record adds `charge_shortfall_cause`. `opt_monitor` passes `charge_outcome.likely_cause`.

## Verification

`tests/test_daily_health.py`; `pytest`, `py_compile` and `pyflakes` green in CI. Live: a taper night at 94% of 100% logs `daily_health` OK.
