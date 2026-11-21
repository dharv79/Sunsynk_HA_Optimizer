# Phase 130: Review fixes: free electricity event (after the 22/11 live run)

Status: Planned, not built

## Why

Fix the free-event findings of the 21/11/2026 code review once the first live free hour (22/11/2026 09:00–10:00) has run on the current code. Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

User decision (21/11/2026): ship after the 22/11 live event so the first live run uses the known 2.3.7 code; don't restart HA after 10:00 on 22/11 while the event is open.

## What

- Restart after free_end re-fires free start: wraps Flux 1 to ~23 h of charging and can leave the event stuck in 'charging' (`opt_events.py:132`, medium)
- Free events entered from the dashboard write to the inverter in monitor mode (do-not-break 16) (`opt_events.py:161`, medium)
- Restore push result is discarded; 'Normal plan restored' / 'cancelled' sent even when the push failed (do-not-break 5) (`opt_events.py:350`, medium)
- A failed sell or free-start push still moves the phase on, so the step is never retried (`opt_events.py:270`, medium)
- Event transitions write back a stale copy after their push and run outside _flux2_lock (`opt_events.py:271`, low)
- schedule_free_event service accepts end before start or in the past, and reports success when scheduling is refused (`opt_events.py:83`, low)

## Verification

New or updated HA-free tests for each fixed item; `pytest`, `py_compile` and `pyflakes` green in CI.
