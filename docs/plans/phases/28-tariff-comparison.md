# Phase 28 — Tariff comparison report

**Status:** Planned, not built (prerequisite passed 13/10/2026). **Size:** M, ~50-80k est. **Origin:** improvement list 2, 06/10/2026 (item 29).

## Goal

Show monthly what Flux cost versus Agile, Go, Cosy and Intelligent for the same usage — possibly the biggest single saving.

## Design

- **Prerequisite check (first):** confirm the Octopus Energy integration exposes half-hourly import/export consumption (and historic Agile rates). If only daily totals exist, scope down to fixed-window tariffs (Go/Cosy) using the logged peak/overnight splits, and say so in the report.
- Pure `planning.cost_under_tariff(half_hourly_kwh, rates)`; tariff rate tables in a dated constant (user-editable option overrides).
- Caveat in the report: battery behaviour would change under another tariff; the comparison holds usage fixed (lower bound on alternatives' benefit for Go/Cosy).
- Monthly section in the Sunday digest after month end (reuse weekly-report plumbing); JSON line to the debug stream.

## Acceptance

- Tests for tariff costing (window boundaries, export rates, missing half-hours → `None` total, never partial-as-complete).

## Prerequisite result (13/10/2026)

The import "previous accumulative consumption" sensor exposes a `charges` attribute: 48 half-hourly `{start, end, consumption}` entries (local time with offset, kWh) for the settled day. Half-hourly costing is feasible for Go/Cosy/Flux from fixed windows. Agile/Intelligent need historic half-hourly rates from the public Octopus API (not yet decided). Export sensor confirmed (same `charges` format, `is_export: true`). Import and export settle on different days (import showed 11 Oct while export showed 12 Oct), so costing must pair them by date and skip incomplete days.
