# Phase 107 — Inverter settings snapshot and change audit

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Notice when an inverter setting changes outside the integration (the installer, the Sunsynk app, a firmware update). The drift check (phase 31) covers only the Flux slots.

## Design

- Weekly read-only `async_read_settings`; store the snapshot; diff against the previous one.
- Ignore the keys the integration writes itself (Flux slots, the gentle-charge current).
- Report each change as "key: old → new" in the debug stream and one notification; keep the last snapshot in the diagnostics download (redacted, phase 93).
- No writes. Monitor mode safe (rule 16).

## Saving

None direct. Catches a changed charge current, low-SOC floor or work mode before it costs a week of wrong behaviour.
