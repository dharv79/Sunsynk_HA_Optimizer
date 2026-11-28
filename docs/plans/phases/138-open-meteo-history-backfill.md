# Phase 138: Backfill the Open-Meteo model from past forecasts

Status: Done, merged (8c211c77441746d38bd830e6d1bcf913e63548b3, PR #263)

## Why

Phase 136 learns this roof's shading and the weather ratio only from nights logged since 2.3.13, so the scorecard's `open_meteo` row needs weeks of data before phase 137 can go live. Open-Meteo keeps the forecasts it issued for past dates, back to 2022, with no key. Combined with the solar output we already hold, that gives months of training and scoring data on day one.

## Decisions

User request (28/11/2026), from the data-sources review. The recommendations were adopted when the user said to build 138–140:

- Backfilled days are shown as a separate `open_meteo_backtest` row and do not count toward phase 137's gate, which still needs at least 7 live days.
- The backfill goes back up to 365 days (not before 2024-01-02, the start of the archive), limited by the solar data we have.

Feasibility check (28/11/2026), passed: the Previous Runs API offers `global_tilted_irradiance_previous_day1` and `cloud_cover_previous_day1` (archived from January 2024), with the Historical Forecast API as the fallback.

## What

- **Feasibility check first.** Confirm which Open-Meteo archive returns `global_tilted_irradiance` with our tilt and azimuth:
  - The Previous Runs API is the closer match to the 01:55 plan, because it keeps the forecast from a day-0 or day-1 lead time.
  - The Historical Forecast API stitches together the first hours of each model run, so it is slightly more accurate than our 01:55 forecast was.

  Record which archive was used and the lead time. If neither archive returns GTI, use `shortwave_radiation` with the same sun model. If that fails too, go On hold.
- **Fetching:** `open_meteo.async_fetch_tilted_archive(…, start, end)`:
  - The same per-string parameters and 2 dp rounding of the location as `async_fetch_tilted`.
  - Requests in chunks of 31 days.
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
