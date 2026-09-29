# Phase 10 — Free electricity event (sell before, refill during)

**Status:** In progress. **Size:** M, ~60-90k est.

## Progress

- `plan_free_event` (planner, `planning.py`, unit-tested) — done, merged (a7fb3ee, PR #23).
- Manual entry path — done, merged (cbee324, PR #25; released in 1.0.11b13; follow-up validation fix fb0d791, PR #27, b14): coordinator state (`free_event`, manual start/end), config options (`free_event_charge_rate_kw`/`free_event_export_rate_kw`), `datetime.py` start/end entities, `button.cancel_free_event`, `sensor.free_event`, execution listeners (sell/free start/end, restart-safe timer resume), do-not-break rule 7 extension, `data_logger.async_log_free_event`, dashboard card, `docs/architecture/free-electricity.md`.
- Octopus auto-detection — not started, still blocked on a live attribute sample of `calendar.octopus_energy_..._greener_nights` from the user's HA (state currently seen as `unavailable`, no session live).

## Scope

When a free electricity period is known, sell battery energy just before it using the export slot (Flux 2), then charge to 100% during it using the import slot (Flux 1). The normal plan is held off for the event and restored afterwards. Decisions below come from the user interview (28/09/2026).

## Decisions (user interview)

| Topic | Decision |
|---|---|
| Trigger | Auto-detect from the Octopus Energy integration's free-electricity session entity (options-flow entity field, blank = off) **plus** manual start/end date-time entities on the dashboard. |
| Auto-detected sessions | Auto-scheduled with an "Event scheduled" notification; a dashboard **Cancel free event** button skips it. |
| Sell floor | Refillable floor: `max(40, 100 − refill_kwh / capacity × 100)`, where `refill_kwh = charge_rate × free_hours`. Never below 40%. |
| Sell window | Ends at free start; only as long as needed to reach the floor at the export rate, capped at the free-period length. (May be revisited: daytime charge/export rates differ from the overnight learned rate.) |
| Rates | New options `free_event_charge_rate_kw` and `free_event_export_rate_kw` (default: configured charge rate). The overnight learned rate is not used by default. Actual rates are logged per event so they can be learned later. |
| SOC already ≤ floor at sell start | Skip the sell; still import during the free period. |
| Overlap with 16:00–19:00 or 02:00–05:00 | Event wins: it holds both slots from sell start to free end. |
| 01:55 plan during an event | Computes and logs as normal, but does not push while an event holds the slots. The plan is pushed when the event ends. |
| Paused during event | Evening export-disable check (30-min); SOC-change and reload one-shot re-pushes. |
| Restore at free end | Re-push the current normal plan (latest Flux 1 plan + standard 16:00 Flux 2 slot), then resume listeners. |
| Notifications | Event scheduled (times, floor, expected kWh sold/refilled); sell start; free start; finished summary (SOC at each stage, kWh exported/imported, estimated £). JSON line to `#sunsynkdebug`. |
| Logging | New `free_event` record type plus a day flag. Learning unchanged for now (tag only); filter later if it skews results. |

## Design

- **Planning (HA-free, `planning.py`, tested):** `plan_free_event(free_start, free_end, soc, capacity_kwh, charge_rate_kw, export_rate_kw)` → `{sell_start, sell_end, sell_floor_soc, expected_export_kwh, expected_refill_kwh, skip_sell}`.
- **State (`coordinator.py`):** persist `free_event` (source, times, floor, phase: scheduled / selling / charging / done / cancelled) so a restart mid-event resumes correctly.
- **Execution (`optimizer.py`):**
  - Point-in-time listeners at sell start, free start and free end.
  - Every push goes via `_async_post_with_status` and `apply_flux_override`:
    - sell: Flux 2 = sell window, `targetSoc` = floor;
    - free: Flux 1 = free window, `targetSoc` = 100;
    - end: restore.
  - Monitor mode: notifications only, no writes. The dry-run button shows any pending event.
  - A guard helper `_free_event_active()` is used by the export-disable check, SOC/reload re-pushes and the 01:55 push.
- **Octopus detection:** read the configured entity's current/next session attributes (sample to be confirmed from the user's HA). Dedup by start time; a cancelled session is not re-armed.
- **Entities / dashboard:** manual start/end `datetime` entities, a "Cancel free event" button, and a "Free event" sensor (phase, times, floor) on the generated dashboard.
- **Do-not-break:** rules 1, 3, 4, 5, 6, 7, 16 and 17 all apply. Rule 7 extends to "no re-push while a free event holds the slots".

## Acceptance criteria

- The planner is unit-tested: floor maths, 40% clamp, sell-window length and cap, skip-sell, and events that cross midnight or the peak window.
- An event survives an HA restart. Normal plan restored at free end. No normal re-push during the event.
- Monitor mode makes no API writes.
- `free_event` record logged. Notifications and debug line sent.
- Docs: new dated section in `export-control.md` (or a new `free-electricity.md` topic plus a CLAUDE.md topic row). Test file row in `testing.md`. Do-not-break rule added if needed.

## Open item for build

Get a sample of the Octopus free-electricity entity's state and attributes from your HA before coding the detector (manual entry does not need this — it's built and doesn't depend on it).
