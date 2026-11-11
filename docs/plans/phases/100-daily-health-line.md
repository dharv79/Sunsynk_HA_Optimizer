# Phase 100 — Daily health line and deploy notice

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Answer "is it working, and which version is running?" from one line, without reading the whole bundle. On 11/11/2026 the only way to tell that 2.0.2 was not yet deployed was to notice missing fields.

## Design

- A `daily_health` record as the first line of the 22:00 bundle and a `sensor.health` (OK / Check / Problem) with the same fields as attributes: version, plan pushed (`api_ok`), charge reached target, records written today against records expected, count of `_guarded` errors by label, API failures, hours since the last SOC update, shadow features active.
- A `deployed` record and notification at startup when the stored version differs from the manifest: old version, new version, and the record types and fields that are new.
- Pure `planning.daily_health(...)` with tests (rule 19). Missing input is `None`, never OK.

## Saving

None. Visibility.
