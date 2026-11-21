# Phase 127: Review fixes: daytime, export pause and peak

Status: Planned, not built

## Why

Fix the daytime, export-pause and peak findings of the 21/11/2026 code review. Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

User decisions (21/11/2026): an export pause that clears before 19:00 restores the normal Flux 2 slot (85%, or the live peak-export slot) once the pause has held at least 30 minutes, from a tested planning helper next to `restore_flux2_slot`, under the Flux 2 lock, clearing the flag only on success; the 16:00 peak export and its re-checks are floored at the higher of Peak minimum SOC and today's peak-cover required SOC, logging which floor applied. The phase 124 wording ('returns to 85% when export re-enables') is corrected by this phase.

## What

- Export pause clears in state only: Flux 2 stays at 100% (or the live peak export stays overwritten) until the 01:55 plan (`opt_daytime.py:271`, medium, plausible)
- day_actuals.evening_export_disabled is almost always False, so pause days leak into overnight-target learning (`opt_capture.py:532`, medium)
- peak_window_usage logs a partial window after any restart in 16:00-19:00, and a clock-shifted window otherwise (`opt_daytime.py:533`, medium)
- Peak cover never retries a failed top-up or hold push (`opt_daytime.py:1054`, medium)
- Cost-aware shadow tally counts every SOC-change check as a half hour of exposure (`opt_daytime.py:188`, low)
- Cost of 1 kWh now uses a 48-50 h reserve (100%) between 00:00 and 02:00 (`opt_daytime.py:899`, medium)
- Daytime logic ignores the phase 68 full-charge skip and still treats the weekday as a full-charge day (`opt_daytime.py:161`, low, plausible)
- Trim sell slot can run up to 45 min (full-day trim 60 min) past 19:00 (`opt_daytime.py:249`, low)
- Native slot-floor hold still stores a Flux 1 slot, so Flux 2 pushes carry it and 16:00 never restores the plan (`opt_daytime.py:1042`, low, plausible)
- 16:00 peak-cover restore pushes Flux 2 outside _flux2_lock and can overwrite a concurrent export pause (`opt_daytime.py:580`, low, plausible)
- Peak export re-check (and the delayed full-day trim) push during a grid outage (`opt_daytime.py:661`, low)
- No-trigger branch overwrites last_flux2_action, so a live peak export or trim shows 'No action' (`opt_daytime.py:265`, low)
- Spare-solar estimate treats an unavailable SOC as a full battery (`opt_daytime.py:930`, low)
- Peak export sizes the peak load differently from peak cover and can sell below what peak cover just bought (`opt_daytime.py:593`, low, plausible)
- Away (holiday) days are not filtered from the peak-load history used to size live peak cover and peak export (`data_logger.py:490`, low, plausible)
- The 16:00 peak export exits during an outage without the skip record that every other early exit writes (`opt_daytime.py:575`, low)
- 16:00-19:00 peak load is measured over a window shifted by up to 29 min (30-min interval is anchored to HA start time) (`opt_daytime.py:517`, low)

## Verification

New or updated HA-free tests for each fixed item; `pytest`, `py_compile` and `pyflakes` green in CI.
