# Phase 140: Forecast confidence from the Open-Meteo ensemble (shadow)

Status: Planned, not built

## Why

The phase 70 P10/P90 band comes from past error in similar months. It cannot tell a settled day from an uncertain one. Open-Meteo's ensemble forecast gives 30–50 alternative runs for the same day, at no cost and with no key. A tight spread means tomorrow's forecast can be trusted. A wide spread means holding more back.

## Decisions

User request (28/11/2026), from the data-sources review:

- Shadow only.
- The overnight target and the low-solar rule are unchanged.
- Whether a tight spread may shrink the hold margin is decided from the scorecard, in a later phase.

Feasibility check (28/11/2026), passed: the ensemble API returns `global_tilted_irradiance` plus `_member01`..`_member50` (ECMWF IFS 0.25) with tilt and azimuth, so the shortwave fallback was not needed.

## What

- **Feasibility check first.** Confirm whether the ensemble API returns `global_tilted_irradiance` with our tilt and azimuth for this location. If it doesn't, use `shortwave_radiation`. Candidate models are ECMWF IFS (51 members) and ICON-EU-EPS. Go On hold if neither works.
- **Fetching:** `open_meteo.async_fetch_ensemble`:
  - the same 2 dp location rounding;
  - one request at 01:55;
  - any failure returns None.
- **Planning:** `planning/solar.py` `om_ensemble_band`:
  - work out each member's daylight total for today;
  - take its ratio to the members' mean;
  - apply the P10, P50 and P90 of those ratios to `om_forecast_kwh`.

  Using ratios means the phase 136 shading model still applies.
- **Logging:** the 01:55 plan logs `om_p10_kwh`, `om_p90_kwh`, `om_spread` (the P90−P10 range as a percentage of P50) and `om_members`.
- **Scorecard:** an `om_ensemble` row with the calibration against the phase 70 band. `daily_health.shadow_features` lists `om_ensemble`.

## Verification

- Tests:
  - the band from synthetic members;
  - a single member or none gives None;
  - the shortwave fallback.
- Live: the 01:55 plan carries the band fields when the Open-Meteo options are set.
