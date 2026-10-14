# Phase 36 — Bill reconciliation

**Status:** Planned, not built. **Size:** S, ~30-45k est. **Origin:** improvement list 3, 14/10/2026 (item 6).

## Goal

Keep the year-to-date figure honest by comparing logged costs with what Octopus actually billed (standing charges, credits such as Happy Hour, VAT changes).

## Design

- Monthly (first Sunday, alongside the tariff comparison): sum logged `daily_cost` for the billing month + standing charge × days; compare with Octopus's account balance/statement sensor if the integration exposes one (feasibility check), else a manual "billed net £" datetime/number entity the user fills in.
- Pure `planning.reconcile_month(logged, standing, billed)` → gap £ and %; alert when |gap| > £2.
- JSON line to the debug stream; human summary to the notify target.

## Acceptance

- Tests: matching month, gap over threshold, missing days → partial flagged, billed unknown → no alert.
