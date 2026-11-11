# Phase 108 — Notification priorities, quiet hours and a morning brief

**Status:** Planned, not built. **Size:** M, ~40-60k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

The optimizer has about 50 notification call sites. Let the user choose what interrupts them.

## Design

- Three levels on every notification: action needed (failed push, charge shortfall, outage), information (plan made, top-up started), report (weekly, monthly).
- Options: minimum level to send at once; quiet hours during which only "action needed" is sent; everything else is held.
- A morning brief at a chosen time: last night's plan and result, today's forecast and spare solar, anything held overnight. One message.
- The debug stream is unaffected. Titles keep the existing convention.

## Saving

None. Visibility: fewer messages, and the important ones stand out.
