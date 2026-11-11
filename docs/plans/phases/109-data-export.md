# Phase 109 — Data export service (CSV)

**Status:** Planned, not built. **Size:** S, ~20-35k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Get the logged history into a spreadsheet without parsing JSONL.

## Design

- Service `sunsynk_optimizer.export_data` (date range, optional record types): writes one CSV per record type, one row per day, to a folder under the HA config directory, and returns the paths.
- A daily-summary CSV that joins plan, actuals, KPIs and cost per day (the paired day plus cost).
- File I/O in the executor; identifiers redacted (phase 93); read-only on the log.
- Optional monthly auto-export on the 1st.

## Saving

None. Reporting.
