# Phase 64 — Pick the bridge target from the forecast-error distribution (newsvendor rule)

**Status:** Done, merged (976f1b4, PR #111), shadow only. A live switch waits for the `newsvendor_target` scorecard to say "ready". **Size:** M. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

On non-low-solar nights, choose the SOC target at the quantile of logged actual/forecast ratios that balances the cost of under-buying (day rate minus cheap rate over efficiency) against over-buying (cheap rate over efficiency minus the export rate, plus wear), instead of using a point estimate from the median correction.

## Why (evidence)

The correction factor moved from 1.224 to 1.395 in a week. Errors run both ways: 5.91 vs 15.2, and 4.87 vs 3.9. The costs are asymmetric: under-buy is about 27.77 - 16.66/0.92 = 9.7p/kWh, and over-buy is about 18.1p minus the export rate (about 15p) plus wear, roughly 8p. A fixed median ignores both. `day_actuals` already holds 30+ forecast/actual pairs.

## Design

Add `planning.quantile_correction(pairs, critical_ratio)` and `newsvendor_target(...)`. Use it only on the `solar_bridge*` branches of `select_target_soc`, so the locked low-solar `min(raw, corrected)` rule is untouched. Pull prices from `tariff_prices_pence` and wear from `_wear_pence`; when a price is missing, fall back to the median (None is never 0). Shadow field `target_soc_newsvendor` goes in `_IMPORT_PLAN_FIELDS`, scored by the backtest.

## Value

Expected-cost reduction on about 150 shoulder/summer bridge nights. A 3-5 point better average target at about 8-10p/kWh is about £8-15 a year, with fewer unused-charge evenings.

## Risk

Needs at least 20 pairs per band to be stable. Seasonal shifts can lag, so weight recent days (reuse `weighted_forecast_correction`).

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
