# Phase 126: Review fixes: overnight plan

Status: In progress

## Why

Fix the overnight-plan findings of the 21/11/2026 code review. Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

User decision (21/11/2026): on the full-charge day with a 7–14 kWh forecast, bridge like a normal day (margin, blend, synthetic ramp, hourly walk), never below the bare bridge, under its own reason code; `explain_plan` must not call a bridge a full charge.

## What

- A re-plan after 02:00 sizes Flux 1 (and the gentle current) as if the charge starts at 02:00, so the charge gets cut short (`opt_overnight.py:560`, medium)
- The 01:55 import plan never runs on the UK spring-forward night (01:00-01:59 local does not exist) (`optimizer.py:248`, low)
- On the full-charge day the bridge target is lower than an ordinary day's (no margin, blend, ramp or hourly walk) (`overnight.py:163`, medium)
- A daytime startup or grid_restored re-plan is logged as the night's import_plan when the 01:55 record is missing (`overnight.py:526`, low)
- Weekday weather scoring uses the UTC weekday and lets a later same-named day overwrite this week's (`opt_overnight.py:199`, low, plausible)
- The charge watchdog ignores a grid outage: it re-pushes during the outage and sends a misleading 'check the time-of-use settings' alert (`opt_overnight.py:878`, low)
- The forecast fallback chains: a stale forecast is reused for as long as the sensor stays down (`opt_overnight.py:803`, low)
- A failed-push retry is silently dropped when an outage starts, and is not re-planned when the grid returns (`opt_overnight.py:759`, low)
- With import_feedback_live on, the notification and explanation show the evening nudge that was not applied (`opt_overnight.py:817`, low)
- hours_to_solar is logged as 0.0 when sun.sun is unavailable, so the twin replays a 30% bridge instead of the band fallback (`opt_overnight.py:629`, low)
- The hourly forecast parser adds the same hour across every date in the attribute (`opt_overnight.py:321`, low, plausible)

## Verification

New or updated HA-free tests for each fixed item; `pytest`, `py_compile` and `pyflakes` green in CI.
