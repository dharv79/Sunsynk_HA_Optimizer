# Phase 122 — Peak minimum SOC, respect a user-raised hold, unsupported read-back

**Status:** Done, merged (fcfb87a, PR #241), released in 2.3.5. **Size:** XS-S, ~20-35k. **Origin:** user request, 20/11/2026.

## Goal

Let the user set the lowest SOC the 14:00 peak top-up/hold aims for at 16:00, stop the 30-minute hold check from undoing a slot the user raised, and stop counting the unsupported Flux read-back as an API failure.

## Why (evidence)

- 19/11: peak cover topped up to 61% (20% floor + 3.0 kWh history × 1.1). The 16:00-19:00 load was 6.1 kWh; SOC hit 20% by 18:00 and 2.1 kWh was bought at the peak rate.
- 20/11: peak cover held 46%. The user raised the slot to 65% in the Sunsynk app; the 30-minute hold re-check (`hold_readback_ok(...) is False`) treated any value other than the hold as drift and wrote 46% back.
- 19/11 `api_health` (first day on 2.3.4): both failures were `GET /api/v1/plant/{id}/income` → HTTP 405. The route doesn't exist, so retrying can't help.

## Design

- `number.peak_minimum_soc` ("Peak minimum SOC", 0-100 %, 0 = off) → `OptimizerState.peak_min_soc` (persisted).
- `planning.peak_required_soc(estimate, min_soc)`: the load-estimate requirement, never below the minimum, capped at 100. `peak_cover_plan(..., min_soc=)` uses it and logs `peak_min_soc`. Below the minimum it becomes a top-up (day-rate charge to the minimum by 16:00); at or above it, a hold.
- `planning.hold_floor_needs_reassert`: the 30-minute check rewrites the hold only when the slot reads **below** it. `planning.hold_floor_restore_due`: at 16:00 the old floor goes back unless the slot reads above the hold (the user's value is left); unreadable → restore, as before.
- `planning.route_unsupported` (HTTP 404/405): `api._authorized_json(..., optional=True)` (only the Flux read-back) logs it under `api_health.unsupported`, counted as a call, not a failure. `read_back_should_stop(n, exc)` stops the read-back at once on it.
- Dashboard: "Peak minimum SOC" tile in Quick actions.

## Tests

`test_peak_cover.py` (minimum raises the requirement, `peak_required_soc`), `test_daytime_hold.py` (reassert only below, user raise kept at 16:00), `test_api_health.py` (unsupported route).
