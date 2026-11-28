# Phase 137: Use the Open-Meteo forecast live once it wins

Status: Planned, not built

## Why

Phase 136 logs the Open-Meteo forecast in shadow. Once the scorecard's `open_meteo` row reads "ready" (its error at least 10% below the live forecast's), it should drive the decisions.

## Decisions

User decision (27/11/2026): one "Open-Meteo forecast live" option; Forecast.Solar stays as the fallback and the low-solar rule keeps `min(raw, corrected)` (do-not-break 8). It feeds:

- the overnight target (daily kWh and the hourly bridge profile);
- peak cover (solar left before 16:00, from the hourly forecast);
- the full-charge day and the spare-solar prompt;
- the solar start time (replacing sunrise + offset).

## What

- Gate (decision 28/11/2026, phase 138): the phase 138 backtest row is shown separately and does not count; at least 7 live scored days are needed.
- `CONF_OPEN_METEO_LIVE` (off by default).
- Use `om_forecast_kwh` / the hourly forecast where those decisions read Forecast.Solar today, with Forecast.Solar as the fallback when Open-Meteo is unavailable.

## Verification

Tests for each decision's input switch and fallback; live: the 01:55 plan shows the Open-Meteo source when the option is on.
