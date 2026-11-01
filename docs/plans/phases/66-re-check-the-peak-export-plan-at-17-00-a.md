# Phase 66 — Re-check the peak export plan at 17:00 and 18:00 using the observed peak load

**Status:** Built (pushes only when `peak_export_live` is on, off by default). **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Re-run `peak_export_plan` during the peak with observed 16:00-now load (from the 16:00 snapshot) and remaining hours, instead of one 16:00 decision based on p75 history.

## Why (evidence)

Weakness 7: one decision at 16:00, never re-checked. On 31 Oct the assumed peak load was 4.0 kWh and the actual 2.4, which leaves 1.6 kWh x margin unaccounted for at 19:00. The 16:00 `meter_snapshot` and `load_daily_used` already give the observed figure at no extra cost.

## Design

Add `planning.peak_export_recheck(soc, reserve_soc, observed_peak_kw, minutes_left, ...)`. Schedule two extra `_guarded` time triggers at 17:00 and 18:00 that re-use `async_run_peak_export`. Push only when the change is at least 0.5 kWh, through `_async_post_with_status`. Shadow under the existing `peak_export_live` flag, with an extra `peak_export` record per check (dedup key date+time).

## Value

In summer and shoulder, about 0.5-1.5 kWh a day more sold at peak instead of carried into the evening at day-rate value. The gap is about peak export minus 27.77 x RTE, roughly £0.05-0.15 a day, so £10-25 a year once live.

## Risk

More pushes during 16-19, so it must respect `_slots_held` and the export pause. Re-checks must never lower the reserve.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built

- `planning.peak_export_recheck(...)`: sizes the rest of the window from the observed 16:00-now load (x1.1 margin), runs `peak_export_plan`, and proposes `lower` (sell more) or, only if the 16:00 plan was pushed, `raise`, when the target moves >= 0.5 kWh. Target is never below reserve + margin. Missing soc or observed load is skipped, never 0.
- `_async_peak_export_recheck` at 17:00 and 18:00 (`async_run_peak_export_recheck`): skipped while slots are held, after the export-disable fires, or with no 16:00 target. Pushes Flux 2 now->19:00 through `_async_push_flux2_action` only when live and not in monitor mode, and keeps the stored `peak_export` record in step so `_peak_export_holds_flux2` owns the window until 19:00.
- New `peak_export_recheck` record (not deduped, two a day).
- Tests: `tests/test_peak_export_recheck.py`.
