# Phase 103 — Measured round-trip efficiency (shadow)

**Status:** Built (PR pending merge). **Size:** S, ~30-45k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Replace the fixed `ROUND_TRIP_EFFICIENCY = 0.85` and the logged `charge_efficiency: 1.0` with a measured figure. Peak cover tops up at day rate to avoid peak rate, which only pays when the real round trip is above about 72% (day price / peak price); a cold battery at low power may be closer to that than 0.85 suggests.

## Vetting

- Battery charge and discharge energy sensors exist (`battery_etoday_charge`, `battery_etoday_discharge`) and phase 75 already reads `BatDischargeTotal`. Check they are reliable before building.

## Design

- Monthly: discharged kWh / charged kWh, corrected for the SOC difference between the start and end of the period; split warm and cold days by battery temperature.
- Log `round_trip_measured` and a shadow line beside each peak-cover and peak-export decision: the gain under the measured figure against the gain under 0.85.
- Add to the scorecard. No live change in this phase.

## Saving (rough, unproven)

£0-10 a year: avoids top-ups that lose money if the measured figure is low; nothing if it is near 0.85.

## Built (13/11/2026)

`planning/efficiency.py`, `soc` on `battery_health` records, weekly recompute in the digest step, `round_trip_shadow` on peak export and cover records, `round_trip` scorecard feature. Detail in `docs/architecture/export-control.md`. Deviations: uses the BMS lifetime totals (the daily meters are not logged); weekly rather than monthly refresh; the measured figure is battery-terminal and the AC figure assumes the inverter legs.
