# Phase 102 — Shiftable-load report

**Status:** Planned, not built. **Size:** M, ~40-60k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Show which regular daytime loads could move to the cheap overnight window or to solar hours, and what that would be worth. The plan index notes that the battery is already filled every night and the remaining lever in winter is the load itself: day-rate import is 7-12 kWh on dark days.

## Design

- From the logged half-hourly import, find blocks that recur on several days at similar times and stand well above the learned profile (washing machine, dishwasher, dryer patterns). No appliance sensors needed.
- Monthly report: each recurring block with its usual time, kWh, and the £ per month if moved to 02:00-05:00 (or to hours with spare solar in summer), using the configured band prices already in the code.
- Check the move is possible: the block plus battery charging must fit under the inverter and grid limit in the cheap window (the code already tracks over-limit import).
- Report only; optional prompt later. Pure function in `planning.py` with tests.

## Saving (rough, unproven)

Depends on the user moving the load. 2 kWh a day moved from day rate to off-peak is about 20p a day, roughly £35 over a winter.
