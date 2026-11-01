# Phase 70 — Self-learned probabilistic solar forecast (own P10/P50/P90)

**Status:** Planned, not built. **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Learn the distribution of forecast error from the household's own history and weather (cloud, rain, temperature, season, forecast size), not just one median correction factor. Decisions can then take a risk-chosen quantile instead of `min(raw, corrected)`.

## Why (evidence)

Errors in the week were both large and in both directions: 27 Oct forecast 5.91, actual 15.2 (+157%); 26 Oct 4.23 vs 2.5; 31 Oct 4.87 vs 3.9. The single factor drifted from 1.224 to 1.395 within a week. Solcast P10 was rejected because Forecast.Solar is the source, so the only way to get an uncertainty band is to learn it locally from `day_actuals` and `import_plan` (forecast_raw, weather already logged for full-charge scoring).

## Design

`data_logger.compute_forecast_quantiles` fits a binned empirical distribution of actual/forecast by forecast band × cloud bucket × month (and later quantile regression), using records from up to 13 months. `planning.select_target_soc` stays unchanged for live decisions, keeping the locked `min(raw, corrected)` rule. A new `forecast_quantiles` field in `_IMPORT_PLAN_FIELDS` logs the P10/P50/P90. The MPC or backtest then uses expected-cost minimisation across the quantiles. Expose a diagnostic sensor.

## Value

Days like 27 Oct cost about £1–1.50 each (unneeded 27.77p-equivalent charge exported at a low rate). Avoiding 30 or more such days a year is worth about £40–60, with better peak-cover calls on top.

## Risk

It needs months of data per bin; the first winter is thin. It must not quietly replace the locked low-solar rule.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
