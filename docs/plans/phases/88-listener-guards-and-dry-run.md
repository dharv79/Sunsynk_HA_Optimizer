# Phase 88 — Listener guards, 22:00 ordering and a pure dry run

**Status:** Done, merged (45271d3, PR #194). **Size:** S, ~25-40k est. **Origin:** code review 11/11/2026.

## Goal

Close the gaps against do-not-break rules 6 and 17, and fix four small logic errors.

## Findings

- Rule 6: these callbacks are registered or called without `_guarded`: `_async_capture_day_actuals` (22:00), the 06:00 morning capture, 06:05 spare solar and its startup call, `_refresh_battery_runtime` and `async_run_flux2_check` inside the SOC listener, `_delayed_full_trim`, and the grid-voltage listener. No line is known to raise today.
- 22:00 order: `_async_capture_daily_cost` (Octopus merge plus a 366-day read) runs before `async_log_day_actuals`. An exception there loses the day's `day_actuals`, `day_kpis` and the bundle, with nothing in `last_error`.
- Rule 17: `async_run_import_plan` calls `_refresh_evening_load` before any `dry_run` check. It reassigns `_evening_load_kw` and `_load_profile` and calls `update_state(load_profile=...)`, so the Test plan button and the `get_plan` service change values that peak cover, the evening reserve and the MPC read.
- The capacity re-measure checks `time in ("05:00", "22:00")` inside `_async_meter_snapshot`, which never runs at 22:00.
- `flux1_end_minutes` rounds whole minutes only: a 15.25-minute need gives a 15-minute slot.
- `plan_free_event` uses wall-clock arithmetic: a free period across a clock change gets the wrong `free_hours` and `sell_start` (helpers `elapsed_seconds` and `shift_hours` already exist).

## Design

- Wrap each body in `_guarded`; at 22:00 log `day_actuals` first and run the cost capture as its own guarded step (keep the year-to-date refresh before the bundle is built).
- Dry run: compute the evening load and profile into locals, no assignment and no `update_state`. Test: state and attributes are identical before and after a dry run.
- Call the capacity estimate from the 22:00 path.
- `math.ceil` for the Flux 1 slot count; DST-safe helpers in `plan_free_event` with a clock-change test.

## Saving

None direct. Protects the daily record and keeps the dry run honest.

## As built (12/11/2026)

All six findings fixed. The dry run skips the evening-load refresh outright (the plan itself reads neither value), rather than computing into locals. No state-identity test: the optimizer has no HA-free harness, so the dry run is covered by the code path only; tests cover the slot rounding and the clock-change case.
