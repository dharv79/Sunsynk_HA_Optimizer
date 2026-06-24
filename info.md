## Sunsynk HA Optimizer

Smart Home Assistant integration to optimise a Sunsynk inverter's charging and export behaviour using solar forecast, battery SOC, and time-of-use tariff windows (Octopus Flux, Go, Economy 7).

### What it does

- **01:55 daily** — Calculates tonight's import window (Flux 1): target SOC, window start/end, charge rate
- **Every 30 minutes** — Controls the export window (Flux 2): disables export when grid draw is high, trims SOC when battery is too full
- **Sunday 18:00** — Scores and selects the best full-charge day for the week ahead

### Adaptive learning

The integration self-corrects over time using historical data:

- **Forecast correction** — scales the raw solar forecast by the actual/forecast ratio over the last 30 days
- **Overnight drain compensation** — adds a safety buffer to the SOC target to cover battery drain between charge end and sunrise (uses the 75th percentile so appliance-heavy nights are covered)
- **Evening SOC nudge** — shifts the target ±5% if the battery consistently ends the day too full or too empty
- **Effective charge rate** — calibrated from historical charging sessions to size the import window precisely

### Requirements

- **SolarSynkV3** — for inverter sensor entities
- **Forecast.Solar** — for daily solar forecast
- **Weather entity** — for full-charge day scoring
- **Sunsynk Power Flow Card** — for the auto-generated dashboard

### Dashboard

Press **Update dashboard** after installing to auto-generate a Lovelace dashboard with power flow, adaptive learning status, 48-hour graphs, and tuning assist sections.

### Manual controls

- **Test plan (dry run)** — recomputes the import plan and sends the full JSON to your app notification, with no inverter push. Safe to press any time of day.
- **Reset baseline** — pushes the baseline Flux windows from config back to the inverter, undoing any optimizer overrides.
- **Update dashboard** — regenerates the Lovelace YAML file.

See the [README](https://github.com/dharv79/Sunsynk_HA_Optimizer/blob/main/README.md) for full installation and configuration instructions.
