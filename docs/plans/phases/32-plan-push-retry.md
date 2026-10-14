# Phase 32 — Retry a failed overnight plan push

**Status:** Done, merged. **Size:** XS, ~20k actual. **Origin:** improvement list 3, 14/10/2026 (item 2).

## Goal

A Sunsynk cloud blip at 01:55 shouldn't cost the night's cheap charge. Today a failed push says "will retry next cycle", but nothing re-pushes Flux 1 until the next night (the 02:20 watchdog only re-pushes if it judges the charge stalled).

## Design

- When the 01:55 (or startup) plan push returns `api_ok=False`, arm a retry every 10 min until 02:40 (`async_call_later`, unpersisted handle like the watchdog re-check), re-pushing `nightly_import_plan.payload`.
- Stop on success (update `api_ok`, clear `last_error`, notify "import plan set (retry N)"), when slots become held, or in monitor mode.
- Pure `planning.plan_retry_due(now_minutes, attempts)` for the schedule.

## Acceptance

- Tests for the retry schedule and stop conditions.
- Respects do-not-break 5, 7 and 16.
