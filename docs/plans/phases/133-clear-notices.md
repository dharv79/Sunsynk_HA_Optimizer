# Phase 133: Clear notices: button and auto-clear for Last error and finished free events

Status: Done, merged (f019ed34ba10b9a56e80360ddbec4aefd24b1a9a, PR #255)

## Why

24/11/2026 the overview still showed a "Flux 2 check skipped — unavailable: grid_pac" Last error from a brief SolarSynk sensor dropout, and "Free electricity event: done" from Sunday's event. Neither had a way to clear it.

## Decisions

User decision (24/11/2026): a button plus auto-clear, released straight away.

- One **Clear notices** button clears Last error and hides a finished (done/cancelled) free-event notice. The event record is kept (the 22:00 capture and overnight plan read it); only a `dismissed` flag is set.
- A failed/skipped check's error clears when that same check next succeeds (`_guarded` label, Flux 2 skipped-inputs).
- A finished free-event notice hides automatically at 00:05 once the day it ended is over.

## What

- `planning.error_resolved`, `free_event_notice_shown`, `free_event_dismiss_due`, `FREE_EVENT_FINISHED_PHASES`.
- `optimizer._guarded` → `_clear_resolved_error`; `opt_daytime` Flux 2 check clears `FLUX2_SKIPPED_ERROR` once inputs are back.
- `opt_events.async_clear_notices`, `async_dismiss_finished_free_event` (00:05 listener).
- `button.clear_notices`, `binary_sensor.free_event_notice`; overview alert gated on the binary sensor and a conditional Clear notices tile.

## Verification

`tests/test_clear_notices.py`; `pytest`, `py_compile` and `pyflakes` green in CI. Live: after Update dashboard, press Clear notices; both cards go and `sensor.free_event` keeps its data.
