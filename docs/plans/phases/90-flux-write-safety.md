# Phase 90 — Flux write safety (slot carry-over and serialised decisions)

**Status:** Planned, not built. **Size:** S-M, ~35-55k est. **Origin:** code review 11/11/2026. Re-vet both findings against live logs before building.

## Goal

Stop one inverter write silently undoing another.

## Findings

- `async_push_flux_override` rebuilds from the config baseline and applies only the slots passed. Peak cover pushes only `flux_1`; the daytime trim pushes only `flux_2`. A trim after a peak-cover hold therefore resets Flux 1 to baseline, while `_peak_cover["pushed"]` stays true, so the hold is not re-pushed that day. Confidence medium: not yet seen in the logs (needs SOC above 85% during a hold).
- Flux 2 decisions are not serialised: the export-pause path reads `evening_export_disabled`, awaits the push, then sets the flag. A SOC event or the 30-minute tick during the push can repeat it; at 16:00 a concurrent pause and peak-export push can leave state saying "disabled" while the export window is live.
- `api.py`: `_ensure_login` and the 401 path both log in with no lock; the 30-minute and 2-hour ticks coincide every two hours.

## Design

- One helper that returns the slots currently in force (tonight's plan, an active peak-cover hold, an active pause) and is used by every single-slot push, as `_async_restore_normal_plan` already does for Flux 1.
- One `asyncio.Lock` around decide-and-push in `async_run_flux2_check`, `async_run_peak_export` and the re-check; re-read state inside the lock; never hold it across a notification.
- A login lock in `api.py` that skips the login when the token has already changed.
- Tests on the slot helper (pure, in `planning.py`, rule 19).

## Saving

Small and unproven: a wiped hold costs the day/peak price gap on the held energy (about 10p per kWh).
