# Phase 20 — Faster, seasonal forecast correction

**Status:** Done, merged (6b1702b, PR #33), shadow only. **Size:** XS, ~25k actual.

**As built:** recency-weighted (14-day half-life), per-band weighted median with global fallback, logged as `forecast_correction_weighted` / `_basis` beside the live factor. The live factor is unchanged; switch over after the phase 14 backtest compares them. Solcast P10 not added (Forecast.Solar is the source). Details: `docs/architecture/import-plan.md`. **Origin:** improvement list 06/10/2026 (item 9).

## Goal

The 30-day median actual/forecast ratio lags in spring/autumn transitions.

## Design

- Pure helper: recency-weighted median (e.g. 14-day half-life) and/or per-band factors; same 0.5–3.0 cap and minimum-day rule.
- Optional: use a pessimistic forecast (Solcast P10 attribute) for `low_solar_forecast_kwh` — keep `min(raw, corrected)` semantics (do-not-break 8).
- Validate with the phase 14 backtest before switching (log old and new factor side by side first).

## Acceptance

- Tests: weighting favours recent days, per-band fallback to global, cap holds.
