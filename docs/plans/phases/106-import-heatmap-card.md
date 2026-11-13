# Phase 106 — Import heat-map card (hour by day)

**Status:** Done, merged (381d418, PR #218). **Size:** M, ~45-70k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

One picture of when the house imports: 28 days down, 48 half-hours across, coloured by import. Shows the cheap window, the daytime gaps and the peak window at a glance.

## Design

- Data from the logged half-hourly import. Served by a websocket command or an authenticated HTTP view, not sensor attributes (28 × 48 values would pass HA's 16 kB attribute limit).
- Bundled card in `www/` in the style of the day chart; toggle between import, export and load; band edges marked; tap a cell to open that day in the day chart.
- Colour scale and accessibility per the existing card conventions; render only when data changes (phase 95 pattern).

## Saving

None. Visibility.

## Built (13/11/2026)

- `planning/heatmap.py` `heatmap_data(kind, half_import, half_export, snapshot_days, today, days, bands)`: days oldest first, 48 half-hour kWh each (None for a missing or short day), `max_kwh` (95th percentile, the colour-scale top) and the Flux import band edges. `load` is derived per day by `planning.day_load_slots` (phase 71) from the meter snapshots and the import shape.
- Served by the websocket command `sunsynk_optimizer/heatmap` (`kind` import/export/load, `days` 1-60, default 28) in `__init__.py`, backed by `optimizer.async_heatmap_data`; read-only. `websocket_api` added to the manifest dependencies.
- Card `www/sunsynk-optimizer-heatmap.js` (registered in `card_resource.CARD_FILES`; new "Heat map" panel view `heat-map` in the generated dashboard): SVG of days by 48 half-hours, one colour per kind, opacity by kWh, dashed band-edge lines, Import / Export / Load buttons, a `<title>` tooltip per cell, redraw only when the response changed (compared as JSON, the phase 95 pattern), 15-minute refresh.
- Tap a cell: the card pushes `/<dashboard>/day-chart?date=YYYY-MM-DD`; the day chart now reads `?date=` (`_applyDateParam`, also when the element is reused).

Verified: the card rendered in headless Chromium against a stubbed `callWS` (27 days of cells plus one no-data row, edges at the Flux window, three loads gave one draw). Not verified on a live HA (the websocket command and the SPA navigation to the day chart).

Deviations: no per-cell keyboard focus (tooltips and the tap target only); the Load view needs the meter snapshots and shows no-data rows for days without them.
