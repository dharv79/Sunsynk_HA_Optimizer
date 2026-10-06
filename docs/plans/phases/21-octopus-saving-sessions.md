# Phase 21 — Octopus Saving Sessions

**Status:** Planned, not built. **Size:** S–M, ~35-60k est. **Origin:** improvement list 06/10/2026 (item 7).

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
