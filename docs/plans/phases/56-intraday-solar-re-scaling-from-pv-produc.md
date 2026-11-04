# Phase 56 — Intraday solar re-scaling from PV produced so far

**Status:** Done, merged (aff091d, PR #115), shadow + advisory. **Size:** M. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

From about 10:00, scale the remaining hourly forecast by pv_etoday ÷ forecast-to-now. Damp the ratio towards 1.0 before 11:00, clamp it to 0.5-2.0, and require at least 1 kWh forecast so far. Peak cover, the 16:00 peak-export surplus and spare-solar prompts use the scaled figure.

## Why (evidence)

async_run_peak_cover uses solar_until_kwh on the raw morning hourly forecast and ignores the PV already produced. pv_etoday is only used as an entity property (optimizer.py:380), never in a decision. The forecast missed by large factors: 5.9 kWh against 15.2 actual on 27 Oct, and 4.9 against 3.9 on 31 Oct (that day's 4.7 kWh top-up at 27.77p).

## Design

Add planning.intraday_forecast_scale(actual_so_far, forecast_so_far, hour). In optimizer, compute forecast_so_far from _hourly_or_synthetic_forecast and actual from day_pv_energy_entity. Pass the scaled remainder into peak_cover_plan and peak_export_plan, and log raw, scaled and scale on the peak_cover and peak_export records for backtest. Writes stay behind the existing peak_cover_live and peak_export_live options. For low-solar safety, use min(raw, scaled) for the cover decision, in line with the min(raw, corrected) principle.

## Value

Fewer unneeded day-rate top-ups on dull days and better-sized peak export on surprise sunny days. Roughly £0.20-0.50 on affected days, about £20-40/yr, plus better appliance prompts.

## Risk

Morning cloud gives a noisy ratio, which the damping and minimums handle. Don't let scaling raise the solar figure used for low-solar protection.

## Vetting note

Confirmed. There is no intraday or pv_etoday use in planning.py or in the optimizer decisions. This merges two duplicate candidates. I added the min() caveat to respect the locked low-solar rule.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (04/11/2026)

`planning.intraday_forecast_scale` (none before 10:00, damped to 1.0 until 11:00, clamp 0.5-2.0, needs >=1 kWh forecast so far) and `scaled_solar_kwh`. Peak cover logs `intraday_scale`, `solar_kwh_to_16_scaled` and `intraday_decision` (its decision on min(raw, scaled)); the live decision is unchanged. The spare-solar refresh multiplies its correction by the scale (advisory, no writes). Peak export has no solar term, so it is untouched. To go live later, compare `intraday_decision` with the actual 16:00 SOC; the min() rule means scaling can only add top-ups, never remove them.
