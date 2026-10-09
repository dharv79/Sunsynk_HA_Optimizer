# Phase 13 — Charge losses and in-window load in Flux 1 sizing

**Status:** Done, merged (2c0833b, PR #37). **Size:** XS, ~25k actual.

**As built:** The learned charge rate is measured as SOC gained per window hour, so it already embeds losses. `planning.charge_efficiency` therefore applies 0.92 only when the nameplate rate is used and 1.0 otherwise. The window is `flux1_end_minutes(energy_needed / efficiency, rate)`, still clamped to 02:15–05:00. In-window load is served from the grid in parallel with the charge, so it does not lengthen the window (no iteration needed). `planning.window_grid_kwh` adds it to the logged `grid_kwh_needed` instead. The plan logs `charge_efficiency`, `energy_needed_kwh`, `window_load_kwh` and `grid_kwh_needed`. Details: `docs/architecture/import-plan.md`. **Origin:** improvement list 06/10/2026 (item 4).

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
