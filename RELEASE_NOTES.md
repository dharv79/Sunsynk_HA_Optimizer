# Sunsynk Optimizer 2.0.0

The first stable release since 1.0.11. It rolls up the 1.0.12 betas (b1 to b50): about 120 changes.

## Before you upgrade

- **Dashboard cards:** the dashboard now bundles its own power-flow card and day chart.
  - You can uninstall the **Sunsynk Power Flow Card** (slipx06) from HACS, unless your own dashboards use it.
  - Keep **ApexCharts Card**: the Plan and Insights views need it.
- **Shadow features:** many new features run in *shadow* first. They log what they would have done and change nothing until you switch them live in the options.
- After updating in HACS, restart Home Assistant and refresh the browser or app.

## Highlights

### New dashboard
- Six views, with every new sensor.
- A bundled Sunsynk power-flow card.
- A **Day chart** tab with previous/next day navigation.
- Bill check, battery health, system health and free-event cards.

### Free electricity events
- Enter a free period on the dashboard (or with a service action).
- The battery sells down beforehand, from a live check of the SOC trend in the last 4 hours, and refills to 100% during the free period.
- The overnight charge is capped so you don't buy electricity you'd only sell back.
- Timers are safe across clock changes. Results are reported, and free import is left out of the KPIs.

### Smarter overnight and peak planning
- **Peak cover:** the battery reaches 16:00 with enough charge for the peak window, holding it early rather than recharging it.
- **Measured charge rate:** taken from the 02:00/05:00 meters.
- **Overnight plan retries:** if a push fails, or SOC can't be read, the plan is retried.
- **Plan explanations:** each plan comes with a plain-English explanation.
- **Battery wear:** its cost is counted in decisions.
- **Gentler charging:** charging is spread across the cheap window.
- **Optional full charge window:** the full 02:00–05:00 Flux 1 window can be used (off by default).
- **Peak export re-check:** the peak export plan is checked again at 17:00 and 18:00.
- **Shadow planners:**
  - A receding-horizon planner (MPC).
  - A newsvendor target.
  - Multi-day low-solar planning.
  - A days-since-last-100% full-charge rule.
  - Deferred trims.
  - An evening-load reserve.
  - An intraday solar scale.
  - Native inverter time-of-use slots.
  - Flexibility-market export events.
  - Agile.

### Forecasting and learning
- A self-learned P10/P50/P90 solar forecast.
- A half-hourly household load forecaster.
- Evening load learned from meter snapshots.
- Overnight drain measured to 05:00.
- Nights with a charge shortfall are left out of the learning.

### Costs, tariffs and reports
- Tariff rates are read from the Octopus integration.
- A monthly tariff comparison: Flux vs Go, Cosy, Intelligent Go and Agile, including standing charges and VAT.
- Tariff price-change alerts.
- Bill reconciliation, and inverter vs smart-meter reconciliation.
- A value-of-the-system report.
- A **Cost of 1 kWh now** sensor.
- An import cause ledger.
- An electrification advisor, and a battery and PV upgrade advisor.
- KPI statistics sensors.
- A monthly shadow go-live scorecard.

### Health and monitoring
- **Grid outages:** detected from grid voltage, with a battery runtime sensor and an outage log.
- **Inverter faults:** a sensor fed by the Sunsynk event list.
- **Battery health:** a state-of-health ledger built from the BMS registers, a capacity estimate, stall hints and fault words.
- **PV strings:** a health monitor comparing the two MPPT strings.
- **Data checks:** a nightly data-integrity audit, with Repairs issues, a data-health sensor and diagnostics.
- **Alerts:**
  - An overnight baseload alert.
  - A daytime load spike alert.
  - Clipped-solar detection.
  - An inverter drift check.
- **Other:** spare-solar prompts and automatic away mode.

### Automation
- New service actions:
  - set or skip tonight's plan;
  - one-night overrides;
  - schedule or cancel a free event;
  - get the plan.

## Changes and removals
- **Octopus Saving Sessions support is removed.** The reward is for importing less, which the battery already does by running the house.
- **Reliability:**
  - API calls have a 30-second timeout.
  - The startup refresh no longer runs off the event loop.
  - The integration version is stamped on every log record.
- **Fixes:**
  - Flux slot conflicts between features.
  - Spook "unknown attribute" warnings.
  - The dashboard "Configuration error" caused by a card-loading race.
