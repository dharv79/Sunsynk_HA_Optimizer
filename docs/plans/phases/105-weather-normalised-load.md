# Phase 105 — Weather-normalised load report

**Status:** Planned, not built. **Size:** S-M, ~30-50k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Say whether the house is using more energy, or whether it is just colder. Daily load ran from 17 to 22 kWh in the week of 06/11/2026 with outside temperatures of 5-8°C.

## Design

- Fit daily load against outside temperature over home days (both are already logged): a base load, a kWh-per-degree slope and the unexplained remainder.
- Monthly report: this month against last month and against the same month last year once 13 months exist, at the same temperature. Flag a base-load or slope change above a threshold.
- A `sensor.load_vs_expected` (today's load against the fitted value).
- Pure function with tests; fewer than 30 home days → `None`.

## Saving

None direct. Finds drift (a failing appliance, a changed habit) that the kWh total hides.
