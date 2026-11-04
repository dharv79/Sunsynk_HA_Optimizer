# Phase 51 — Free events: DST-safe timers, a watch_from guard and sold-energy split

**Status:** Done (04/11/2026), merged (0708689, PR #107). Design and verification: `docs/architecture/free-electricity.md`. **Size:** S. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Arm the free-event watch, free-start and free-end callbacks with async_track_point_in_utc_time, or compute delays on UTC datetimes, so a clock change can no longer shift them by an hour. Refuse a sell before watch_from. Log sold_from_battery_kwh and sold_from_solar_kwh so 'expected vs sold' compares like for like.

## Why (evidence)

optimizer._arm_free_event_timers (lines ~2872-2891) computes `(watch_from - now).total_seconds()` on two datetimes that share the same HA local tzinfo. Python ignores utcoffset when both datetimes have the same tzinfo, so the subtraction is wall-clock and is off by 1 h across a DST change. On 25 Oct (clocks back) selling started at 08:50, before watch_from 09:00. That fits a watch armed before 02:00 firing an hour early. free_event_sell_decision (planning.py:1319) has no time guard, and it projects only battery SOC, so the 10.7 kWh sold against 6.0 kWh expected can't be split into battery and solar. Phase 10 is the open in-progress phase, and its manual event has had no clean live test.

## Design

In _arm_free_event_timers, convert with dt_util.as_utc before subtracting, or switch to async_track_point_in_utc_time. Audit any other wall-clock subtraction on aware datetimes in optimizer.py. Add a planning.free_event_in_watch(now, free_start) guard and call it in _async_do_free_event_watch. In the free_event result record, add sold_from_battery_kwh ((soc_at_sell_start − soc_at_free_start) × capacity) and sold_from_solar_kwh (metered export − battery part). Add HA-free tests for an event on 25 Oct 2026 and on 29 Mar 2026 that is armed before 01:00, and for a sell attempt before watch_from.

## Value

Twice a year, prevents a sell or refill window running 1 h out. That could sell 1-2 kWh more or less than planned, or misalign the free_end restore (roughly £0.30-1.50 per affected event). It also gives clean debug data for the phase 10 live sign-off.

## Risk

Low. The timing fix is HA-free and testable. It must not change the slot-holding rules (do-not-break 7).

## Vetting note

Confirmed. _arm_free_event_timers subtracts dt_util.now() from dt_util.as_local(...) values that share the same tzinfo, which is naive wall-clock arithmetic in Python, and free_event_watch_from is a plain timedelta subtraction. This merges the 'DST-safe timing' and 'sell that accounts for load/solar' candidates. I dropped the learned-bias part, because there is only one event so far.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
