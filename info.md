## Sunsynk HA Optimizer

Optimises a Sunsynk inverter's overnight charging and evening export for the Octopus Flux tariff, using the solar forecast, battery state of charge (SOC), learned household load and the tariff's price bands.

### What it does

- **01:55**: plans the 02:00–05:00 cheap charge. It buys just enough to reach the morning solar, or fills to 95–100% on dull days.
- **Through the day**: keeps an evening reserve until 02:00, tops up before the 16:00 peak if needed (optional), pauses evening export under heavy load, and trims an over-full battery without selling energy bought cheaply overnight.
- **Free electricity events**: caps the overnight charge, sells any surplus just before the window and refills to 100% during it.
- **Health**: grid outage and inverter fault detection, battery state of health, PV string health and a nightly data check.
- **Weekly**: picks a full-charge day from the forecast and sends a cost digest.
- **Learns** forecast bias, overnight drain, charge rate and household load from your own history, with a separate away profile.
- **Reports** real Octopus costs, daily KPIs, battery savings, a tariff comparison, upgrade advice and a bill check against your statement. It also sends alerts for price changes, unusual load and failed inverter writes.

New decision logic ships in observe-only mode first. Monitor mode runs everything without writing to the inverter.

### Requirements

- **SolarSynkV3**: inverter sensor entities
- **Forecast.Solar**: daily solar forecast
- **Weather entity**: full-charge day scoring
- **ApexCharts Card** (HACS frontend): for the generated dashboard's charts. The power-flow card and day chart are bundled.
- Optional: **Home Assistant Octopus Energy** for real costs and tariff rates

### Manual controls

- **Test plan**: a dry run of tonight's plan that sends the JSON to your notification, with no inverter write. Safe at any time.
- **Reset to baseline**: pushes the configured baseline Flux windows back to the inverter.
- **Update dashboard**: regenerates the Lovelace YAML.
- **Service actions**: set or skip tonight's charge, schedule or cancel a free event, get the plan.
- **Free event start / end** and **Cancel free event**: enter or cancel a free-electricity window.

See the [README](https://github.com/dharv79/Sunsynk_HA_Optimizer/blob/main/README.md) for installation and configuration.
