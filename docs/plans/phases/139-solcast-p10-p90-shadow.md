# Phase 139: Use Solcast's low and high estimates (shadow)

Status: Done, merged (53e232996cb3178510fda6b55c2f3dc87b93efd8, PR #264), released in 2.3.14

## Why

The Solcast integration already publishes a low (P10) and a high (P90) estimate next to `pv_estimate`, but `opt_overnight` reads only `pv_estimate`. Solcast's own uncertainty band could make low-solar nights safer than our learned P10 (phase 70), which is based on history.

This phase only helps Solcast users. Forecast.Solar, which this install uses today, publishes no P10 or P90. For this install the phase logs nothing until Solcast is configured.

## Decisions

User request (28/11/2026), from the data-sources review:

- Shadow only.
- The low-solar rule keeps `min(raw, corrected)` (do-not-break 8).
- Feeding the target is a later decision, made from the scorecard.

## What

- Extend the Solcast parser in `opt_overnight` to sum `pv_estimate10` and `pv_estimate90` beside `pv_estimate`. The daily `estimate10` and `estimate90` attributes are the fallback. Anything missing is None.
- The 01:55 plan logs `solcast_p10_kwh` and `solcast_p90_kwh`.
- The shadow scorecard gets a `solcast_band` row. It shows the calibration (share of days below P10, inside the band, above P90) against our phase 70 P10/P90, using the existing calibration helper in `planning/forecast.py`.
- `daily_health.shadow_features` lists `solcast_band` when tonight's plan carried the band.

## Verification

- Tests:
  - the parser with and without the P10 and P90 keys;
  - the Forecast.Solar path stays unchanged;
  - the scorecard calibration.
- Live (Solcast only): the 01:55 plan carries both fields.
