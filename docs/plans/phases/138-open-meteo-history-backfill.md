# Phase 138: Backfill the Open-Meteo model from past forecasts

Status: Planned, not built

## Why

Phase 136 learns this roof's shading and the weather ratio only from nights logged since 2.3.13, so the scorecard's `open_meteo` row needs weeks of data before phase 137 can go live. Open-Meteo keeps the forecasts it issued for past dates, back to 2022, with no key. Combined with the solar output we already hold, that gives months of training and scoring data on day one.

## Decisions

User request (28/11/2026), from the data-sources review. Settle these in the build interview:

- Whether backfilled days count toward phase 137's "ready" gate, or are shown separately. Recommendation: show them as a separate `open_meteo_backtest` row, and require at least 7 live days before 137 can go live.
- How far back to go. Recommendation: up to 365 days, limited by the solar data we have.

## What

- **Feasibility check first.** Confirm which Open-Meteo archive returns `global_tilted_irradiance` with our tilt and azimuth:
  - The Previous Runs API is the closer match to the 01:55 plan, because it keeps the forecast from a day-0 or day-1 lead time.
  - The Historical Forecast API stitches together the first hours of each model run, so it is slightly more accurate than our 01:55 forecast was.

  Record which archive was used and the lead time. If neither archive returns GTI, use `shortwave_radiation` with the same sun model. If that fails too, go On hold.
- **Fetching:** `open_meteo.async_fetch_tilted_history(start, end)`:
  - The same per-string parameters and 2 dp rounding of the location as `async_fetch_tilted`.
  - Requests in chunks of about 90 days.
  - Any failure returns None.
- **Hourly solar actuals:**
  - Before 2.3.13, take them from HA's long-term statistics for the PV energy sensor (`recorder` `statistics_during_period`, hourly `change`).
  - From 2.3.13 on, take them from `hourly_profile.pv_kwh`.
  - Where the sensor has no statistics, fall back to daily `day_actuals.actual_solar_kwh`. In that case only the daily score is computed and the shading model is not trained.
- **Training and scoring** in `planning/solar.py`:
  - `om_backtest` replays the days walk-forward: each day is predicted only from the days before it, so nothing leaks from the future.
  - It scores the Open-Meteo forecast against the live corrected forecast from the logged `import_plan` records.
  - The resulting model seeds the phase 136 learner, which carries on with live nights as now.
- **Trigger:** a one-off "Backfill solar forecast history" button, plus an automatic run at startup when the model has fewer than 14 days. The result is stored so the backfill never repeats.
- **Output:**
  - One `om_backfill` debug record with the day count, archive, lead time, mean absolute error and basis.
  - A scorecard row as decided above.
  - Diagnostics show the backfill status.

## Verification

- Tests in `tests/test_open_meteo_backfill.py`:
  - walk-forward with no look-ahead;
  - the daily-only fallback;
  - chunking;
  - a failed fetch returns None.
- Live: press the button. An `om_backfill` record appears, `om_basis` reads `learned` on the next 01:55 plan, and the scorecard shows the backtest row.
