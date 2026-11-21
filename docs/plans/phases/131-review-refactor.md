# Phase 131: Refactor: Flux 2 decision and free-event timer helpers, split the import plan, shared helpers

Status: Planned, not built

## Why

Behaviour-preserving refactors from the 21/11/2026 code review, after the bug fixes (125–130). Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

User decision (21/11/2026): bug fixes first, then these targeted refactors.

## What

- Split the ~375-line async_run_import_plan; its rate and window glue is where most of these defects sit and it has no tests (`opt_overnight.py:344`, low)
- Move the Flux 2 decision (pause / already paused / restore / trim) into a tested planning helper (`opt_daytime.py:116`, low, plausible)
- Free-event lifecycle and restart resume have no tests; move the timer plan into planning (`opt_events.py:113`, low)
- band_price is a line-for-line copy of core.price_at (`reports.py:86`, low)
- Duplicated ratio-sample builders (and price_at vs band_price) should share one helper (`forecast.py:133`, low)
- shadow_scorecard repeats the same error-ratio verdict block three times (`kpis.py:525`, low)
- Dead `force`/day<=7 gates in the monthly senders and a repeated 'last month' calculation (`opt_reports.py:656`, low)
- Four copies of the guarded settings read; use the existing _async_api_read (`opt_monitor.py:473`, low)
- SunsynkOptimizerSensor spreads each sensor over five places; a description table would remove the drift risk (`sensor.py:298`, low, plausible)

## Verification

New or updated HA-free tests for each fixed item; `pytest`, `py_compile` and `pyflakes` green in CI.
