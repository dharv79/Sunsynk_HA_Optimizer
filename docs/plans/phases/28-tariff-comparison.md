# Phase 28 — Tariff comparison report

**Status:** Planned, not built. **Size:** M, ~50-80k est. **Origin:** improvement list 2, 06/10/2026 (item 29).

## Goal

Show monthly what Flux cost versus Agile, Go, Cosy and Intelligent for the same usage — possibly the biggest single saving.

## Design

- **Prerequisite check (first):** confirm the Octopus Energy integration exposes half-hourly import/export consumption (and historic Agile rates). If only daily totals exist, scope down to fixed-window tariffs (Go/Cosy) using the logged peak/overnight splits, and say so in the report.
- Pure `planning.cost_under_tariff(half_hourly_kwh, rates)`; tariff rate tables in a dated constant (user-editable option overrides).
- Caveat in the report: battery behaviour would change under another tariff; the comparison holds usage fixed (lower bound on alternatives' benefit for Go/Cosy).
- Monthly section in the Sunday digest after month end (reuse weekly-report plumbing); JSON line to the debug stream.

## Acceptance

- Tests for tariff costing (window boundaries, export rates, missing half-hours → `None` total, never partial-as-complete).
