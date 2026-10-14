# Phase 38 — "Cost of 1 kWh now" sensor

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** improvement list 3, 14/10/2026 (item 8).

## Goal

One live number automations can use: what using 1 kWh right now costs, and the cheapest hour left today.

## Design

- Pure `planning.marginal_cost_pence(now, soc, spare_solar, prices, wear)`: spare solar → lost export price; battery above the evening reserve → replacement cost (off-peak / efficiency + wear); otherwise the current grid band price.
- Sensor `sensor.cost_of_1_kwh_now` (p/kWh) with attributes `source` (solar/battery/grid) and `cheapest_hour_today`; refreshed on the 30-min tick and SOC change (no polling).
- Dashboard tile; docs example automation (dishwasher when below N p).

## Acceptance

- Tests per source, missing price → `None` (never 0p).
