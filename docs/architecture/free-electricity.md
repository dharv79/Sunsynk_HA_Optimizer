# Free electricity event

Read only when changing the free-electricity-event logic (phase 10, `docs/plans/phases/10-free-electricity-event.md`).

## Manual entry (28/09/2026)

Sell battery energy just before a known free-electricity period (Flux 2), then charge to 100% during it (Flux 1). The normal plan is held off for the event and restored afterwards. This slice covers **manual entry only** — the Octopus auto-detect trigger is still blocked on a live attribute sample from the user's HA (`greener_nights` calendar entity).

**Entry point:** two `datetime` entities, `datetime.free_event_start` / `datetime.free_event_end` (`datetime.py`). Setting both (end after start, start in the future) calls `SunsynkOptimizer.async_try_schedule_manual_free_event`, which delegates to `async_schedule_free_event(free_start, free_end, source="manual")`.

**Planning:** `planning.plan_free_event` (HA-free, unit-tested) computes the sell floor, sell window and expected export/refill kWh from current SOC, `CONF_BATTERY_CAPACITY`, and `CONF_FREE_EVENT_CHARGE_RATE_KW` / `CONF_FREE_EVENT_EXPORT_RATE_KW` (both default to `CONF_CHARGE_RATE` when unset).

**State:** `OptimizerState.free_event` (source, times, floor, expected kWh, `phase`: `scheduled` → `selling` (skipped if SOC is already at/below the floor) → `charging` → `done`/`cancelled`). Persisted, so a restart mid-event resumes correctly via `_arm_free_event_timers`, called both right after scheduling and from `async_setup` on startup — each callback re-arms with a 0-second delay if its trigger time already passed.

**Execution (`optimizer.py`):** three point-in-time `async_call_later` callbacks (sell start, free start, free end), each pushed via `async_push_flux_override` → `_async_post_with_status` (do-not-break rule 5):
- sell start: Flux 2 = now→free_start, `targetSoc` = floor.
- free start: Flux 1 = now→free_end, `targetSoc` = 100.
- free end: `_async_restore_normal_plan` re-pushes the latest logged Flux 1 (`last_import_plan.payload.flux_1`) plus the standard 16:00–16:15 Flux 2 slot, then clears the manual entities.

**Guard:** `_free_event_active()` (`phase` in scheduled/selling/charging) pauses the 30-minute Flux 2 check, the SOC-change listener, and the 60 s post-setup reload one-shot — do-not-break rule 7. The 01:55 plan still computes and logs (`held_by_free_event` field on the plan record) so it's ready to push the moment the event ends, but the push itself is skipped while held.

**Cancel:** the `button.cancel_free_event` entity → `async_cancel_free_event`. Restores the normal plan only if the event had already taken a slot (`selling`/`charging`); a still-`scheduled` cancel has nothing to restore.

**Logging:** `data_logger.async_log_free_event` appends a `free_event` record at `scheduled` / `done` / `cancelled`. Not yet read back by any adaptive learning — tag only, per the phase decisions; filter it out of drain/nudge history later if it turns out to skew results.

**Not yet built:** Octopus auto-detect (blocked), do-not-break rule additions beyond rule 7, `testing.md` row (no new HA-free pure logic was added — `plan_free_event` itself was already tested in phase 10's first slice).

## Validation and startup fixes (29/09/2026)

- **Manual entry warnings:** the start/end entities are set one at a time, so validating the pair on every set warned against a stale counterpart (four warnings in a row in `#homenotifications`). `planning.manual_free_event_error(changed, ...)` now judges only the field just set: a past start warns; a start at/after a stale end waits silently; an end at/before start warns.
- **Startup "plan skipped" warning:** the 60 s post-setup one-shot (`_async_initial_refresh`) ran before SolarSynkV3 had polled, so the SOC entity was unavailable and `_async_skip_plan` notified. It now defers (up to 5 x 60 s, `_INITIAL_REFRESH_MAX_RETRIES`) while SOC is unavailable, then falls through to the normal skip/notify.
