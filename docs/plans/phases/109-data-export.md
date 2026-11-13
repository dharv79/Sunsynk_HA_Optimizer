# Phase 109 — Data export service (CSV)

**Status:** Built (PR pending merge). **Size:** S, ~20-35k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Get the logged history into a spreadsheet without parsing JSONL.

## Design

- Service `sunsynk_optimizer.export_data` (date range, optional record types): writes one CSV per record type, one row per day, to a folder under the HA config directory, and returns the paths.
- A daily-summary CSV that joins plan, actuals, KPIs and cost per day (the paired day plus cost).
- File I/O in the executor; identifiers redacted (phase 93); read-only on the log.
- Optional monthly auto-export on the 1st.

## Saving

None. Reporting.

## Built (13/11/2026)

`planning/export.py` (`export_rows`, `to_csv`, `daily_summary_rows`, `record_date`); service `sunsynk_optimizer.export_data` (`start`, `end` dates, optional `types`) handled by `async_service_export_data` in `opt_reports.py`.

- Writes one CSV per record type plus `daily_summary.csv` (the paired day joined with that day's `day_kpis`, columns prefixed `kpi_`) into a new `sunsynk_optimizer_export/<timestamp>/` folder under the config directory, and returns `{folder, files, from, to}` (response optional). Default range is the last 90 days. The summary is added only when no `types` filter is given.
- Read-only on the log (it reads through `async_load_records` and `async_load_paired_days`); file writing runs in the executor. Unknown record types are refused. Every cell is masked with `sensitive_values(config)` (phase 93), and nested values are written as compact JSON.
- Not blocked in monitor mode: it writes nothing to the inverter (rule 16 concerns inverter writes).

Deviation: the optional monthly auto-export on the 1st is not built (it needs a new config option and flow step); the service can be called from an automation instead.
