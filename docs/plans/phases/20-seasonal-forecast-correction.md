# Phase 20 — Faster, seasonal forecast correction

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 06/10/2026 (item 9).

## Goal

The 30-day median actual/forecast ratio lags in spring/autumn transitions.

## Design

- Pure helper: recency-weighted median (e.g. 14-day half-life) and/or per-band factors; same 0.5–3.0 cap and minimum-day rule.
- Optional: use a pessimistic forecast (Solcast P10 attribute) for `low_solar_forecast_kwh` — keep `min(raw, corrected)` semantics (do-not-break 8).
- Validate with the phase 14 backtest before switching (log old and new factor side by side first).

## Acceptance

- Tests: weighting favours recent days, per-band fallback to global, cap holds.
