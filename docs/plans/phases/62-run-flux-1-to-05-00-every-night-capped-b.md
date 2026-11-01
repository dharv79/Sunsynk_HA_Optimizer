# Phase 62 — Run Flux 1 to 05:00 every night, capped by the target

**Status:** Planned, not built. **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

End the overnight window at 05:00 on bridge nights too, with the cap set at the target SOC. The inverter then holds the battery at the target and the grid runs the house at the off-peak rate until 05:00. Today the window ends early (e.g. 03:45) and the battery runs the house at a loss.

## Why (evidence)

On 25 Oct the target was 59 with Flux 02:00-03:45, and the morning SOC was 53. That gap of about 6 points is the 03:45-05:00 load being taken from the battery. That energy was charged at 0.92, is discharged at 0.92, and has to be replaced later at the 27.77p day rate. Low-solar nights already run to 05:00 (flux1_end_minutes is only shortened on bridge nights), and the gentle-charge path already does this. It would also make the bridge window independent of the fragile charge-rate learning.

## Design

planning.flux1_end_minutes gains a full_window flag that returns 300 when the plan is not a drain/feedback test night. optimizer.async_run_import_plan sets it behind a new CONF_FULL_WINDOW_LIVE (default off). In shadow, log the 'full_window_end' and the 05:00 SOC it would give (target vs actual 05:00 snapshot) in import_plan, and add both fields to _IMPORT_PLAN_FIELDS (do-not-break 10). The window_grid_kwh cost estimate needs no change. Planning test.

## Value

About 0.5-0.8 kWh more in the battery at 05:00 on each bridge night. That is worth ~(27.77−~17)p + loss ≈ 7-10p a night, or ~£15-25/yr over the ~200 bridge nights from spring to autumn.

## Risk

The drain-learning code (_charge_calibration_end_hours) assumes the battery drains after flux1_end, so with a full window the drain sample must come from no-charge nights only. Check that the drain p75 does not collapse to 0.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
