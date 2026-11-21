# Phase 125: Review fixes: monitor mode and push results

Status: In progress

## Why

The 21/11/2026 code review found inverter writes in monitor mode (do-not-break 16) and state
that claimed a push worked when it had failed (rule 5), plus dry-run side effects (rule 17).

- The full-charge-day trim pushed Flux 2 in monitor mode, and its 1-hour timer did nothing when
  the battery filled before 15:00 (trims before 16:00 are refused by the 14/10 trim guard).
- The Reset baseline button pushed in monitor mode and during a free event, and cleared the
  export-pause flag even when its push failed.
- A failed export-pause push still set `evening_export_disabled`, so the pause was never retried
  and later checks reported `api_ok: True`.
- Test plan (dry run) and `get_plan` wrote `last_error` and sent "plan skipped" when SOC or the
  forecast was missing.
- With the gentle charge live, a plan push that failed for good left the grid-charge current
  throttled until 05:00 under the previous night's full-current window.
- In monitor mode the daily health stayed "Check" (no `import_plan` record, a stale push result)
  and the 06:20 audit kept a permanent "missing import_plan" issue open.
- The health line read the export shadow flag without its default (rule 15).
- Restored settings were written as `'60.0'` instead of the inverter's `'60'`.

## Decisions (user, 21/11/2026)

- Reset baseline is refused like the services: monitor mode, or a free event holding the slots;
  a notification says why.
- The full-charge day stays at 100% until 16:00 (trim guard kept): the trim timer fires an hour
  after 99.5%, and never before 16:00.

## What

- `_monitor_refuses` backstop in `_async_post_with_status` and `_async_write_setting`; only writes
  that undo our own (`async_restore_charge_current`, the daytime-hold restore and rollback, the
  free-event restore) pass `allow_in_monitor=True`.
- Full-day trim: no arming in monitor mode; the timer re-checks monitor mode and outages;
  delay `planning.full_trim_delay_s` (an hour, and not before 16:00).
- `planning.reset_refusal` for the button; the pause flag is cleared only when the reset landed.
- `_async_push_flux2_action` keeps the previous pause flag when the push failed.
- `_async_skip_plan(msg, dry_run, notify)` returns `{"skipped": msg}` for a dry run, with only a
  🧪 notification when `notify`.
- `_async_undo_gentle_charge` restores the grid-charge current when the plan push fails with no
  retry due, and after the last retry; the final notification says whether it was restored.
- `planning.audit_daily_types(monitor)`; `records_today`/`audit_days` take the types;
  `daily_health(monitor=True)` ignores the push result.
- `planning.setting_text` writes whole numbers without `.0`.

## Verification

`tests/test_monitor_guards.py` (helpers plus AST checks for the backstop, the allowlist, the reset
and pause flags, the trim guard and the dry-run skip). Live: nothing changes in auto mode except
the full-day trim timing and the pause retry after a failed push.
