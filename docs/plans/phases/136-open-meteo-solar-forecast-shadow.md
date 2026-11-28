# Phase 136: Open-Meteo solar forecast with a learned sun and shading model (shadow)

Status: Done, merged (6ea9f0ab01f44a0f96e9e330c141b3909888d75f, PR #261), released in 2.3.13

## Why

Forecast.Solar's daily error averaged 4.16 kWh on a ~7 kWh week-day (15–21/11/2026), and the live and weighted correction factors disagree (1.29–1.34 vs 0.75–0.89). The phase 82 cloud bins never had data: the 01:55 plan logged `cloud_cover_pct: null` every night because Met.no's daily forecast has no `cloud_coverage`.

## Decisions

User decision (27/11/2026), after an interview:

- Source: Open-Meteo (free, no key) hourly sunlight on the panels, plus a sun-position model that learns this roof's shading.
- Rollout: shadow first, scored on the shadow scorecard; phase 137 makes it live once it wins.
- Once live it feeds the overnight target, peak cover (solar to 16:00), the full-charge day and spare solar, and the solar start time (phase 137).

## What

- `open_meteo.py`: `async_fetch_tilted` (hourly `global_tilted_irradiance` per panel string, plus `cloud_cover` on the first; location rounded to 2 dp; any failure → None).
- `planning/solar.py`: `om_strings`, `compass_to_open_meteo`, `sun_position` (NOAA approximation), `sun_bin`, `om_reference_hours` (GTI/1000 × kWp; each value belongs to the hour before its stamp), `om_cloud_hours`, `om_training_hours`, `om_learn` (shade per sun bin from clear hours ≤30% cloud, weather ratio per cloud bucket), `om_predict`, `om_solar_start` (first time forecast solar covers the house load), `daylight_cloud_cover`, `hourly_counter_kwh`.
- `hourly_profile` rows gain `pv_kwh` (solar counter interpolated to hour boundaries); the 30-min sampler reads the PV day counter.
- 01:55 plan (shadow, never feeds the target): `om_forecast_kwh`, `om_basis` (learned / default / unavailable), `om_solar_start`, `om_days`, `om_cloud_pct`; tonight's plan also logs an `om_forecast` record (hourly reference, cloud and forecast) for learning. Failures never stop the plan.
- Cloud cover: when the daily weather forecast has none, the mean over today's daylight hours of the hourly forecast is used (feeds the phase 82 cloud bins).
- Shadow scorecard row `open_meteo`: mean absolute error vs the live corrected forecast. `daily_health.shadow_features` lists `open_meteo` when tonight's plan carried a forecast.
- Options: `pv_tilt`, `pv_azimuth` (compass, 180 = south), optional `pv2_tilt`, `pv2_azimuth`, `pv2_share_pct`; with `pv_kwp`. Blank = off.

## Verification

`tests/test_open_meteo_forecast.py`; `pytest`, `py_compile` and `pyflakes` green in CI. Live: set tilt, direction and kWp; the next 01:55 plan line carries `om_forecast_kwh` and `om_basis: "default"`, and an `om_forecast` record is logged. After a few days `om_basis` reads `learned` and the scorecard shows an `open_meteo` row.
