# Phase 21 — Octopus Saving Sessions

**Status:** Done, merged (e24e307, PR #44). **Size:** M, ~55k actual. **Origin:** improvement list 06/10/2026 (item 7).

## Goal

During Octopus Saving Sessions, run the house from the battery and export the rest, earning the session reward. The mirror image of the phase 10 free event.

## Design

- Reuse phase 10 machinery (persisted event state, timers, restart resume, slot holding — do-not-break 7): before the session, charge in the off-peak window (or day rate if cheaper than the reward); during it, Flux export to a floor.
- Trigger: manual start/end first (dashboard datetimes like phase 10); then auto from the Octopus integration's saving-session event/sensor.
- Pure planner `plan_saving_session(...)` in `planning.py` with tests.

## Prerequisite

Phase 10 manual free-event live test passed.

## Acceptance

- Tests for the planner and validation; dry run shows the plan; monitor mode makes no writes.

## Outcome (09/10/2026)

Built at the user's request before the phase 10 live test (still outstanding — watch the first live session). `plan_saving_session` sets the floor (20% when reward + export beats rebuying at the day rate, else the evening reserve), the session-day 01:55 boost to 100% and an optional day-rate top-up that never runs into the peak. Manual datetimes plus auto-detect from the Octopus saving-session / Power Down events entity (joined events only, Octopoints ÷ 8 = p/kWh). A saving session holds the slots only while topping up or exporting; monitor mode makes no writes; the dry run shows the pending session. Details: `docs/architecture/free-electricity.md`.
