# Phase 13 — Charge losses and in-window load in Flux 1 sizing

**Status:** Planned, not built. **Size:** XS–S, ~15-30k est. **Origin:** improvement list 06/10/2026 (item 4).

## Goal

`energy_needed = (target − soc) × capacity` assumes every imported kWh lands in the battery and the house uses nothing 02:00–05:00. Both are false; the window ends short and the shortfall is bought at day rate later.

## Design

- Pure helper: `grid_kwh_needed = energy_needed / charge_efficiency + window_load_kw × window_hours` (iterate once: window length depends on kWh).
- `charge_efficiency`: config constant default 0.92, or derived from the existing `compute_effective_charge_rate_kw` history if it already embeds losses (check first — avoid double-counting).
- `window_load_kw`: learned rate from phase 12 if built, else config.
- Still clamped 02:15–05:00; low-solar 05:00 extension unchanged.

## Acceptance

- Tests: efficiency 1.0 + zero load reproduces today's minutes; losses lengthen the window; clamp holds.
- Plan JSON logs `charge_efficiency` and `window_load_kwh`.
