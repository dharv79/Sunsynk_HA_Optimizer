# Phase 46 — HA service actions and actionable notifications

**Status:** Done (04/11/2026). **Size:** M. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Register `sunsynk_optimizer.set_tonight_target`, `skip_tonight`, `schedule_free_event`, `cancel_free_event`, `pause_export_until` and `get_plan` (which returns the dry-run plan). Add mobile action buttons to key notifications, for example 'Charge to 100 tonight' and 'Undo export pause'.

## Why (evidence)

Grep finds no `async_register` service code. Overrides need the options flow, which reloads the integration. Free events are keyed by hand through datetime entities (the 1 Nov 11:00–13:00 event), and Octopus auto-detect is blocked on a live entity sample. A `schedule_free_event` service lets the user's own automation (email, calendar) fill that gap now. The entry points already exist: `async_schedule_free_event` (optimizer.py:2914), `async_cancel_free_event` (:3101) and `async_run_import_plan(dry_run=True)`.

## Design

Add `services.yaml` and register the services in `__init__.py` with voluptuous schemas. Store one-night overrides via `coordinator.update_state` (`target_override={date, soc}`, `skip_date`) and apply them in a new `planning.apply_user_override()` step after the free-event cap, with tests. `get_plan` uses `SupportsResponse.ONLY` on the dry-run path (rule 17). Every write goes through `_async_post_with_status`. Handlers refuse writes in monitor mode and while `_slots_held` is true. Tag override days in `import_plan` so drain and nudge learning can exclude them. Handle notification `data.actions` with a `mobile_app_notification_action` listener.

## Value

Free events can be automated today, about £1.44 of free import per event (25 Oct). It also removes an options reload for every manual tweak, and opens Assist and Node-RED control.

## Risk

Overrides must not break rules 5, 7, 16 or 17, and must be excluded from learning. Action buttons need the companion app.

## Vetting note

Two candidates merged (services plus actionable notifications, and services for automations and Assist). No service registration exists. All the internal methods it needs are confirmed in optimizer.py. It is the best workaround for the blocked phase 10 auto-detect.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (04/11/2026)

Services `set_tonight_target`, `skip_tonight`, `schedule_free_event`, `cancel_free_event`, `get_plan` (response only, dry run, rule 17). `planning.next_plan_date` / `apply_user_override` apply after the free-event cap; override nights carry `user_override` in `import_plan` and are excluded from drain and nudge learning. Writes refuse in monitor mode and while slots are held. Not built: `pause_export_until` (clashes with the automatic re-enable) and notification action buttons.
