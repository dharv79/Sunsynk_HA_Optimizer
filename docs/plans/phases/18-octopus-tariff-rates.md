# Phase 18 — Read tariff rates from the Octopus integration

**Status:** Done, merged (b4adfc9, PR #43). **Size:** S, ~30k actual. **Origin:** improvement list 06/10/2026 (item 6).

## Goal

Cost-aware export and phase 17 use user-entered `charges`. Read live Flux rates instead so decisions track price changes without reconfiguring.

## Design

- Optional config: Octopus current import/export rate sensors (blank = keep `charges`, graceful degrade).
- Pure helper resolves the price for a time window from the sensor's rate attributes; falls back to `peak_import_price_pence_per_kwh` on `charges`. `None` when unknown, never 0p (do-not-break 14).
- Log `price_source` on decisions.

## Acceptance

- Tests: sensor rates used, fallback to charges, missing → None.
- Shadow mode decision unchanged (locked).

## Outcome (09/10/2026)

Built as designed: `octopus_import_rates_entity` / `octopus_export_rates_entity` accept the current-rate sensor or the day-rates event; per-band fallback to `charges`; `price_source` on the peak-window flux2 action, `day_kpis` and `peak_export`. Shadow decision unchanged. Details: `docs/architecture/export-control.md`.
