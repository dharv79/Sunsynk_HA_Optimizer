# Sunsynk HA Optimizer

Home Assistant integration that optimises a Sunsynk inverter's overnight charging and evening export for the Octopus Flux tariff. It uses the solar forecast, battery state of charge (SOC), learned household load and the tariff's price bands.

Latest version: see [Releases](https://github.com/dharv79/Sunsynk_HA_Optimizer/releases). Stable releases look like `2.0.1`. Versions with `b` in them (for example `1.0.12b50`) are betas; turn on **Show beta versions** in HACS to get them.

## Features

**Overnight charging**
- 01:55 plan for the 02:00–05:00 cheap window. It buys just enough to reach the morning solar, or fills to 95–100% when the forecast is poor (under 7 kWh).
- Charge window sized from the learned charge rate, charge losses and battery temperature.
- Weekly full-charge day, picked on Sunday from the weather forecast and re-checked daily against the solar forecast.
- Charge watchdog at 02:20, automatic retry if a plan push fails, and a 2-hourly check that the inverter still matches the plan.
- Gentle charging (optional, observe-only by default): the lowest grid charge current that still reaches the target by 05:00.

**Day and evening**
- Peak cover (optional, observe-only by default): before 16:00, makes sure the battery will last the 16:00–19:00 peak, holding charge early or topping up at the day rate.
- Evening reserve: the battery keeps enough charge at 19:00 to run the house until the 02:00 charge.
- Evening export pause when grid draw is high (watt trigger, with a cost-based trigger running in observe-only mode).
- Daytime trim: above 85% SOC, sells down towards 82% for 45 minutes. It never goes below the evening reserve or, before 16:00, below the SOC bought cheaply overnight. On the full-charge day it holds 100% for an hour first.
- Optional peak export: sells the surplus above the evening reserve at the peak export rate, re-checked at 17:00 and 18:00 (observe-only by default).

**Free electricity events**
- Enter a free-electricity window (start and end) on the dashboard or with a service action.
- The overnight charge on that day stops at what the house needs until the window, so you don't buy power you'd only sell back.
- From 4 hours before, it watches the battery and sells any surplus just before the window, then charges to 100% during it and restores the normal plan.

**Learning**
- Forecast correction, overnight drain buffer, evening SOC nudge, effective charge rate and learned household load, all from your own history.
- Separate away profile (manual switch or automatic from people and calendar), so holidays don't skew the everyday learning.

**Health and monitoring**
- Grid outage detection (from grid voltage), battery runtime left and an outage log.
- Inverter fault sensor from the Sunsynk event list.
- Battery state of health from the BMS, measured usable capacity and fault words.
- PV string health: alerts when one MPPT string keeps producing well below its usual share.
- Nightly data check (06:20) that raises a Repairs issue when logged data is missing.

**Costs and reports**
- Optional Octopus Energy link for real electricity and gas cost, a running year-to-date total and tariff rates read from Octopus.
- Daily efficiency KPIs and what the battery saved you, a weekly cost digest, a monthly tariff comparison (Flux vs Go, Cosy, Agile, Intelligent and others) and a bill check against your Octopus statement (see [Bill check](#bill-check)).
- Alerts: tariff price changes, overnight load well above normal, daytime grid import spikes, low battery capacity and spare midday solar.
- "Cost of 1 kWh now" sensor for automations (run appliances when energy is cheap).
- Monthly battery and solar upgrade advisor and electrification (heat pump) advisor.
- Optional AI Task weekly insight.

**Shadow lab**
- New planners run in observe-only mode and report what they would have done: a receding-horizon (MPC) planner, a self-learned P10/P50/P90 solar forecast, a half-hourly load forecaster, Agile vs Flux, native inverter time-of-use slots and flexibility-market export events. A monthly scorecard shows which are worth switching on.

**Safety**
- Monitor mode runs all the logic without writing to the inverter.
- Every inverter write reports success or failure. A failed push sends a "⚠️ … NOT applied" notification.

## Requirements

Install and configure these first.

1. **[SolarSynkV3](https://github.com/martinville/solarsynkv3)**: inverter sensors, such as `sensor.solarsynkv3_<inverter_serial>_battery_soc`, `_grid_pac`, `_pv_mppt0_power`, `_pv_mppt1_power`, `_pv_etoday` and the daily load and grid meters. Setup checks that `battery_soc` and `grid_pac` exist.
2. **Forecast.Solar**: daily forecast, for example `sensor.energy_production_today`. A tomorrow forecast sensor and an hourly forecast are optional.
3. **A weather entity**, for example `weather.forecast_home`, used for full-charge day scoring.
4. **[ApexCharts Card](https://github.com/RomRider/apexcharts-card)** (HACS frontend): needed by the generated dashboard's charts. The power-flow card and the day chart are bundled with the integration, so there is nothing else to install.

Optional: **[Home Assistant Octopus Energy](https://github.com/BottlecapDave/HomeAssistant-OctopusEnergy)** for real costs, tariff rates and the tariff comparison.

## Installation via HACS

1. Open **HACS**, then the top-right menu, then **Custom repositories**.
2. Add `https://github.com/dharv79/Sunsynk_HA_Optimizer` with category **Integration**.
3. Search for **Sunsynk Optimizer** and click **Download**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & Services → Add Integration** and choose **Sunsynk Optimizer**.

## Configuration

Setup asks for the essentials. Everything else is under **Configure** on the integration. The options flow has four steps: main settings, tariff rows 1–4, tariff rows 5–8, and the baseline Flux 1 (import) and Flux 2 (export) windows.

### Important ID mapping

These two values are different and must not be swapped.

| Use | Field |
|---|---|
| Sunsynk API writes | **Plant ID** (numeric) |
| SolarSynkV3 sensor entity IDs | **Inverter serial** |

### Main settings

| Setting | Notes |
|---|---|
| Sunsynk username / password | Cloud account, validated at setup |
| Plant ID / Inverter serial | See the ID mapping above |
| Weather entity, Forecast.Solar sensor | Required |
| Hourly forecast sensor + attribute, tomorrow's forecast sensor | Optional; improve the morning bridge and multi-day planning |
| Notify service / Notify target | Human notifications, for example `notify.notify` |
| Daily data report notify target | Optional; machine-readable JSON debug lines (see below) |
| Operation mode | `auto` (writes to the inverter) or `monitor` (no writes) |
| Battery capacity (kWh), charge rate (kW), nominal voltage (V) | Defaults 10 kWh, 3 kW, 51.2 V |
| Average consumption: weekday / weekend / away (kW) | Defaults 0.75 / 0.9 / 0.3. Replaced by the learned load once enough days are logged |
| Hours after sunrise until solar covers load | Sets the morning solar start |
| Default full-charge day | Used until the first Sunday pick. Default Wednesday |
| Evening export disable threshold (W) | Default 1500 |
| Evening export disable cost threshold (p/hour) + observe-only switch | Cost-based trigger; observe-only by default |
| Free event charge rate / export rate (kW) | Used to plan free-electricity events. Blank = the charge rate |
| Inverter export limit (W) | Turns on clipped-solar detection; 0 = off |
| Battery purchase cost (£) + lifetime throughput (kWh) | Optional wear cost in export decisions |
| Auto away: person entities, calendar + keyword, hours away | Optional automatic away mode |
| Spare solar notification threshold (kWh) | Optional morning prompt |
| Octopus cost sensors (import, export, gas) | Optional real cost and year-to-date total. Use the "previous accumulative cost" sensors |
| Bill usage period start day | 1–28, default 1 (calendar month). Set it to the first date in your bill's "We have charged you" lines |
| Octopus import / export rates entities | Optional; tariff prices read from Octopus instead of the tariff rows |
| Octopus accumulative consumption sensors + comparison overrides | Optional monthly tariff comparison |
| Upgrade prices, PV size, gas prices, heat pump price | Optional; add payback figures to the monthly upgrade report |
| Flexibility event entity + payment (p/kWh) | Optional; logs flexibility export events (observe only) |
| AI Task weekly insight | Optional, off by default; needs an AI Task provider (HA 2025.7+) |

### Behaviour switches

New decision logic ships in observe-only mode first. It logs what it would do without changing the inverter, until you turn its option on:

| Option | Default |
|---|---|
| Cost-aware export threshold — observe only | On (observing) |
| Adjust the overnight target from day-rate import | Off (observing) |
| Sell the surplus above the evening reserve at the peak rate | Off (observing) |
| Charge gently across 02:00–05:00 | Off (observing) |
| Hold the target to 05:00 every night (grid runs the house after the charge) | Off (observing) |
| Size the 02:00 window from the charge rate measured on the meters | Off (observing) |
| Size the evening reserve from the learned 16:00–22:00 load | Off (observing) |
| Size the evening reserve and peak cover from the learned half-hourly load | Off (observing) |
| Trim only the solar that would overflow and sell the rest at the peak rate | Off (observing) |
| Skip the weekly full charge when the battery reached 99% in the last 7 days | Off (observing) |
| Top up before 16:00 if the battery would not last the peak | Off (observing) |
| Notify at 05:00 when overnight load is high | On |

## How it works

### Overnight plan (01:55)

Target SOC:

| Condition | Target |
|---|---|
| Normal day | Enough to reach the time solar covers the house load, plus the overnight drain buffer and evening nudge |
| Forecast under 7 kWh, winter-like | 100% |
| Forecast under 7 kWh, other seasons | 95% (the drain buffer can take it to 100%) |
| Weekly full-charge day, sunny | As a normal day; solar fills the battery to 100% |
| Weekly full-charge day, dull | 100% from the grid |
| Sunrise time unavailable | 80% / 85% / 95% by season |

Low-solar decisions use the more pessimistic of the raw and corrected forecasts.

The window starts at 02:00 and ends when the battery should reach the target, rounded up to 15 minutes, between 02:15 and 05:00. Dull days always use the full window. House load during the window is supplied by the grid alongside the charge, so it doesn't lengthen the window.

Learning (active once enough days are logged):
1. **Forecast correction**: median actual/forecast ratio over 30 days.
2. **Overnight drain buffer**: 75th percentile of the SOC lost between charge end and 06:00.
3. **Evening SOC nudge**: ±5% if the battery keeps ending the day too full or too empty.
4. **Effective charge rate**: measured SOC gained per hour of charging, then derated when the battery is cold.
5. **Learned household load**: replaces the configured consumption.

### Daily schedule

| Time | What happens |
|---|---|
| 01:55 | Overnight plan pushed to Flux 1 |
| 02:00, 05:00, 16:00, 19:00, 22:00 | Meter snapshots for the daily KPIs |
| 02:20 | Charge watchdog (is the battery actually charging?) |
| 05:00 | Overnight house load check |
| 06:00 | Morning SOC recorded |
| 06:05 | Spare solar estimate |
| 06:10 | Tariff price-change check |
| 06:20 | Data check (missing records raise a Repairs issue) |
| Before 16:00 | Peak cover check |
| 16:00 | Peak export decision, re-checked at 17:00 and 18:00 |
| 18:00 | Full-charge day choice (Sundays, plus a daily re-check) and the monthly reports when due |
| 22:00 | Day actuals, KPIs, PV string check and the data report |
| 22:05 | Agile vs Flux comparison (observe only) |
| Every 30 min and on SOC change | Evening export pause, daytime trim, daytime import alert, cost of 1 kWh now |
| Every 2 h | Inverter drift check |
| On grid voltage change | Grid outage start and end |

### Full-charge day scoring

Sunday 18:00 scores Monday–Friday: `100 − cloud − 0.7 × rain`, with +25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow, ±3 for temperature, and Thursday −5 / Friday −15. The daily 18:00 re-check can move the day to tomorrow if tomorrow's solar forecast is good enough to fill the battery and scores better.

## Entities

| Entity | What it shows |
|---|---|
| `sensor.selected_full_charge_day` | This week's full-charge day (attributes: scores) |
| `sensor.import_plan_end` | Tonight's plan (attributes: the full plan) |
| `sensor.next_import_window`, `sensor.current_soc_target` | Window and target |
| `sensor.flux_2_action` | Last export-window action |
| `sensor.consumption` | Overnight, daily and peak-window load, grid import/export, cost |
| `sensor.free_event` | Current free-electricity event |
| `sensor.spare_solar_today` | Expected spare midday solar (kWh) |
| `sensor.cost_of_1_kwh_now` | What using 1 kWh now costs (p/kWh) |
| `sensor.operation_mode` | `auto` or `monitor` |
| `sensor.import_causes_today` | Today's grid import split by cause (kWh) |
| `sensor.battery_runtime` | Hours the battery would run the house now |
| `sensor.kpi_*` | Daily KPIs with long-term statistics: avoidable import £, peak export £, self-sufficiency %, unused charge, over-limit import |
| Diagnostic sensors | Forecast correction, drain adjustment, evening SOC adjustment, effective charge rate, battery capacity estimate, battery state of health, PV string 1 share, data health gaps, solar forecast P50, load profile, MPC plan, Agile vs Flux saving, inverter TOU differences, flex event offer, upgrade advisor, shadow scorecard, last error, last updated |
| `binary_sensor.grid_outage`, `binary_sensor.inverter_fault` | Grid outage and inverter fault |
| `binary_sensor.evening_export_disabled`, `binary_sensor.monitor_only_mode` | Status flags |
| `switch.away_mode` | Away (holiday) profile |
| `datetime` Free event start / end | Manual free-event entry |
| `number.billed_last_month` | Enter your latest bill (£) to check it against the logged cost |
| Buttons | Test plan (dry run), Run choose best day, Reset to baseline, Update dashboard, Cancel free event |

**Test plan** recomputes tonight's plan and sends the JSON to your notify target. It doesn't push, log or change state, so it's safe to press at any time.

### Service actions

| Action | What it does |
|---|---|
| `sunsynk_optimizer.set_tonight_target` | Charge to a set SOC tonight only (learning ignores that night) |
| `sunsynk_optimizer.skip_tonight` | Hold the battery where it is tonight (no grid charge) |
| `sunsynk_optimizer.schedule_free_event` / `cancel_free_event` | Enter or cancel a free-electricity window |
| `sunsynk_optimizer.get_plan` | Dry-run tonight's plan and return it (nothing is pushed or logged) |

## Bill check

1. Under **Configure**, set **Bill usage period start day** to the first date in your bill's "We have charged you" lines (for example 8 for a bill covering 8 Sep – 7 Oct).
2. On the dashboard's **Energy & cost** tab, enter the bill amount in **Bill check**:
   - with the gas cost sensor set: the bill's **Total charges for bill** (charges minus export credit);
   - without it: electricity charges minus export credit.
   - Leave out one-off credits (such as Weekend Happy Hour) and payments.
3. Enter it before the next start day. It's checked against the latest complete bill period before the day you enter it.
4. A notification shows the gap. Over £2 on a complete period gives a warning; a period with missing logged days is marked partial.

## Dashboard

1. Press **Update dashboard**. This writes `sunsynk_optimizer_<entry_id>.yaml` to your config folder and shows the snippet in a notification.
2. Add it to `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    sunsynk-optimizer:
      mode: yaml
      filename: sunsynk_optimizer_<entry_id>.yaml
      title: Sunsynk Optimizer
      icon: mdi:battery-heart-variant
      show_in_sidebar: true
```

3. Restart Home Assistant.

The dashboard has seven tabs:

| Tab | Shows |
|---|---|
| Overview | Power flow, alerts (only when something needs attention), tonight's target and why, today's best time for big loads, quick actions |
| Day chart | One day's solar, battery, load, grid power and SOC, with the day's totals; ◀ / ▶ and a date picker go to earlier days |
| Plan | The full "why this plan?" table, P10/P50/P90 solar, learned vs actual house load, what the system has learned |
| Energy & cost | Today's meters, overnight/peak/day use, settled and year-to-date cost, import causes, bill check, 30-day KPI trends |
| Battery & system | State of health and BMS detail, measured capacity, runtime left, grid/inverter/data health, live values |
| Shadow lab | Scorecard, MPC plan chart, Agile vs Flux, inverter TOU check, flex event offer, upgrade advisor (nothing here writes to the inverter) |
| Controls | Away mode, test plan, one-night full charge or skip (each asks to confirm), free event entry |

Press **Update dashboard** again after an update that changes the layout.

## Data and debug report

Decisions and actuals are logged to `{config}/sunsynk_optimizer_data/YYYY-MM.jsonl`. Files older than 13 months are pruned. Every record carries the integration `version`.

If a **daily data report target** is set, the integration posts one JSON line per record each day at 22:00 (for example to a Slack channel). The records are tonight's plan, the morning state, day actuals, KPIs, peak-window usage, peak cover, peak export and the latest settled cost. A weekly cost summary follows on Sundays at 18:00. Record types are described in [docs/architecture/data-logging.md](docs/architecture/data-logging.md).

## Notifications

Titles start with "🔋 Sunsynk:", for example: import plan set, grid outage, PV string output low, full-charge day chosen or moved, evening export paused, battery trim, topping up before the peak, free event scheduled, started or finished, spare solar today, overnight load high, daytime grid import, Flux prices changed, tariff comparison, bill check, battery value, weekly insight. A failed inverter write is titled "⚠️ Sunsynk: … NOT applied".

## Troubleshooting

- **Setup won't save**: SolarSynkV3 must be installed with `battery_soc` and `grid_pac` loaded for your inverter serial.
- **Dashboard shows wrong sensors**: check the Inverter serial vs Plant ID mapping, then press **Update dashboard**.
- **Plan not applied**: check the **Last error** sensor, the Plant ID and your Sunsynk credentials.
- **Learning not active yet**: corrections stay neutral until enough days exist (about 5–7). The drain buffer uses a safe fallback until then.
- **HACS shows no information**: use **Update information** from the HACS menu.

## Updating

1. Download the update in HACS.
2. Restart Home Assistant.
3. Press **Update dashboard** if the release notes mention dashboard changes.

## Notes

This integration builds on SolarSynkV3 and Forecast.Solar. It does not replace them. The `cryptography` library used for the Sunsynk login ships with Home Assistant.

Design notes and the end-to-end logic are in [docs/logic.md](docs/logic.md) and [docs/architecture/](docs/architecture/).
