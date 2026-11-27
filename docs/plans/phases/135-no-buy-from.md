# Phase 135: No-buy from: start the no-buy window earlier on demand

Status: Planned, not built

## Why

27/11/2026 a saving session ran 15:00–16:00. Peak cover buys (top-up or hold) until 16:00, so it would import during the session.

## Decisions

User decision (27/11/2026), after an interview:

- A dashboard date-time picker, **No-buy from**, for one day; it clears itself after 19:00. Only the start moves; the end stays 19:00.
- Top-ups and holds finish by the new start, and the 16:00 target also covers the extra hours' house load, so the battery runs the house from the start to 19:00.
- Release straight away.

## What

- `planning.no_buy_start_minutes`, `no_buy_extra_load_kwh`, `peak_cover_last_log_minutes`; `peak_cover_retry_due(..., last_log)`, `peak_cover_startup_retry_due(..., peak_start)`.
- `state.no_buy_from`; `datetime.no_buy_from`; Quick actions tile.
- `opt_daytime`: peak cover window, slot end, minutes to peak and load use the start; `_cover_hold_slot` ends at it; `_arm_no_buy_timer` ends a live cover at the start; 19:01 `async_expire_no_buy_from`.

## Verification

`tests/test_no_buy_window.py`; `pytest`, `py_compile` and `pyflakes` green in CI. Live: set 15:00 for today; the peak-cover slot ends at 15:00 and no grid import 15:00–16:00 while SOC is above the floor.
