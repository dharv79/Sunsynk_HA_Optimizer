# Phase 83 — High grid voltage export guard

**Status:** Planned, not built (feasibility-gated). **Size:** S, ~25-35k est. **Origin:** idea sweep 08/11/2026 (idea 7).

## Goal

Stop planning exports the inverter cannot deliver. When the grid voltage sits near the over-voltage trip, the inverter derates or disconnects export, so a held-back battery sells nothing.

## Vetting (08/11/2026)

- The code reads `grid_phase0_voltage` only for outage detection (`grid_online_from_voltage`). Phase 29 detects clipping at the export limit but not voltage derating.
- Feasibility check first: scan the logged voltage and `peak_export` days for derating (export below the limit with the battery full and voltage above ~253 V). If no cases appear in a month of data, mark the phase On hold.

## Design

- Pure `planning.voltage_derate_suspect(voltage_v, export_w, export_limit_w, soc)`; tally minutes per day in an unpersisted counter; log `export_derated_min` in `day_actuals`.
- Only if the tally is non-zero on several days: shadow-log a "skip the hold, trim instead" decision for `peak_export`. No live change in this phase.

## Savings estimate (rough, unproven)

Probably £0-10/yr. Worth building only if the feasibility scan finds derated days.
