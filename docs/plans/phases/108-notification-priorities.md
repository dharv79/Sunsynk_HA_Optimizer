# Phase 108 — Notification priorities, quiet hours and a morning brief

**Status:** Built (PR pending merge). **Size:** M, ~40-60k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

The optimizer has about 50 notification call sites. Let the user choose what interrupts them.

## Design

- Three levels on every notification: action needed (failed push, charge shortfall, outage), information (plan made, top-up started), report (weekly, monthly).
- Options: minimum level to send at once; quiet hours during which only "action needed" is sent; everything else is held.
- A morning brief at a chosen time: last night's plan and result, today's forecast and spare solar, anything held overnight. One message.
- The debug stream is unaffected. Titles keep the existing convention.

## Saving

None. Visibility: fewer messages, and the important ones stand out.

## Built (13/11/2026)

`planning/notify_policy.py` (`notification_level`, `in_quiet_hours`, `release_time`, `delivery`, `hold_item`, `morning_brief`); the gate is in `SunsynkOptimizer.async_notify` (`optimizer.py`), which now classifies, then sends or holds, and hands sending to `_async_deliver`. `async_notify_debug` calls `_async_deliver` directly and an explicit `target` is never gated, so the debug stream is unaffected.

Options (`const.py`, `config_flow._base_schema`; defaults change nothing): `notify_min_level` (report / info / action), `notify_quiet_start` and `notify_quiet_end` (HH:MM, both or neither), `morning_brief_time` (HH:MM, blank = off).

- Levels come from the title, not per call site (about 50 sites): a ⚠️ or 🧪 title and "grid outage", "stopped short", "inverter event", "not running" and "free event" are action; weekly/monthly report titles are report; everything else is information. Action always sends.
- Held items (below the minimum level, or non-action inside quiet hours) are stored in `state.held_notifications` (newest 40, text clipped) and sent in one "morning brief" at the brief time, else when quiet hours end (then only if something was held). The brief lists last night's plan and morning SOC, today's forecast and spare solar, and the held lines. With no release time configured a held message is dropped, not queued.
- The brief itself is sent past the gate; held items are cleared only when it went out.

Deviations: levels are title-based, so a new notification with an unusual title defaults to information; a test of the optimizer-level gate is not possible without HA (the decision logic is tested).
