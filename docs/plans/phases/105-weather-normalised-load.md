# Phase 105 — Weather-normalised load report

**Status:** Done, merged (e82c016, PR #214). **Size:** S-M, ~30-50k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Say whether the house is using more energy, or whether it is just colder. Daily load ran from 17 to 22 kWh in the week of 06/11/2026 with outside temperatures of 5-8°C.

## Design

- Fit daily load against outside temperature over home days (both are already logged): a base load, a kWh-per-degree slope and the unexplained remainder.
- Monthly report: this month against last month and against the same month last year once 13 months exist, at the same temperature. Flag a base-load or slope change above a threshold.
- A `sensor.load_vs_expected` (today's load against the fitted value).
- Pure function with tests; fewer than 30 home days → `None`.

## Saving

None direct. Finds drift (a failing appliance, a changed habit) that the kWh total hides.

## Built (13/11/2026)

`planning/weather_load.py`: `weather_load_rows` (home days with `day_actuals.day_load_kwh` and `day_kpis.outside_temp_c`, away days from `import_plan` dropped), `fit_load_temp` (least squares on heating degrees, base 15.5 °C like `heating_scale`; negative slope treated as none; 30 days minimum), `load_vs_expected` (today against the fit of every other day), `month_vs_fit`, `monthly_load_report`, `load_report_text`.

- Daily: `_async_capture_load_vs_expected` in the 22:00 flow stores `load_vs_expected` state (not logged to JSONL; it is derived from records) behind `sensor.load_vs_expected` (state = % above/below expected, attributes = expected/actual kWh, base, slope, temperature). Away days are skipped.
- Monthly: `_async_send_load_report` in `_async_monthly_reports` reads 420 days. A month is compared with the fit of all *other* home days (so the month cannot explain itself) and flagged when its mean difference is at least 8% and 2 standard errors; the base load of its own fit is compared with last month's (flag at 15%). Debug JSON line always; human notification only when something is flagged. Last-year comparison appears once that month has 10 days.

Deviations: the fit is over heating degrees only (no wind or solar-gain term); the same-month-last-year figure is the same test applied to that month, not a separately normalised value.
