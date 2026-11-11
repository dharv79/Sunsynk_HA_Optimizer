# Phase 106 — Import heat-map card (hour by day)

**Status:** Planned, not built. **Size:** M, ~45-70k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

One picture of when the house imports: 28 days down, 48 half-hours across, coloured by import. Shows the cheap window, the daytime gaps and the peak window at a glance.

## Design

- Data from the logged half-hourly import. Served by a websocket command or an authenticated HTTP view, not sensor attributes (28 × 48 values would pass HA's 16 kB attribute limit).
- Bundled card in `www/` in the style of the day chart; toggle between import, export and load; band edges marked; tap a cell to open that day in the day chart.
- Colour scale and accessibility per the existing card conventions; render only when data changes (phase 95 pattern).

## Saving

None. Visibility.
