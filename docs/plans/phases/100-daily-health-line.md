# Phase 100 — Daily health line and deploy notice

**Status:** Built (PR pending merge). **Size:** S, ~25-40k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Answer "is it working, and which version is running?" from one line, without reading the whole bundle. On 11/11/2026 the only way to tell that 2.0.2 was not yet deployed was to notice missing fields.

## Design

- A `daily_health` record as the first line of the 22:00 bundle and a `sensor.health` (OK / Check / Problem) with the same fields as attributes: version, plan pushed (`api_ok`), charge reached target, records written today against records expected, count of `_guarded` errors by label, API failures, hours since the last SOC update, shadow features active.
- A `deployed` record and notification at startup when the stored version differs from the manifest: old version, new version, and the record types and fields that are new.
- Pure `planning.daily_health(...)` with tests (rule 19). Missing input is `None`, never OK.

## Saving

None. Visibility.

## Built (13/11/2026)

`planning/health.py` (`daily_health`, `records_today`, `deploy_changes`), `sensor.health`, the 22:00 health line and the startup `deployed` record and notice. Detail in `docs/architecture/entities-dashboard.md`. Deviations: the record is in the debug bundle and state only (not the JSONL log); the deploy notice lists new record types, not new fields; API failures count push and settings-write failures until phase 101.
