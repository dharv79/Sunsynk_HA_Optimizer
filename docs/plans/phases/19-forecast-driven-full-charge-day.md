# Phase 19 — Pick the full-charge day from the solar forecast

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 06/10/2026 (item 8).

## Goal

The Sunday 18:00 selector scores weather text with weekday penalties. Prefer the day with the most forecast solar kWh so 100% comes from PV, not grid.

## Design

- Optional config: multi-day solar forecast source (e.g. Solcast day-N sensors or a forecast attribute list). Check what the user's forecast integration exposes before building.
- Pure scorer: forecast kWh primary, existing weather score as tie-break; keep weekday penalties optional.
- Fallback to the current scorer when no multi-day forecast is configured.

## Acceptance

- Tests: highest kWh wins, tie-break, fallback path identical to today.
