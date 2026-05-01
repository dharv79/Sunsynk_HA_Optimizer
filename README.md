# Sunsynk HA Optimizer

Smart Home Assistant integration to optimise Sunsynk inverter charging and export behaviour using solar forecast, battery SOC, time-of-use windows, and automated Flux control.

Current stable release: **v1.0.7**
Current beta release: **v1.0.8b3** (adaptive learning beta)

> **Beta notice:** Features marked *(beta)* below are available in v1.0.8b3 only. The current stable release (v1.0.7) does not include them. Install the beta via HACS by selecting the pre-release version.

## Features

- Smart overnight import planning driven by solar forecast and battery SOC
- Adaptive learning — self-corrects forecast bias, overnight battery drain, and evening SOC outcomes over time *(beta)*
- Dynamic Flux 2 export control with evening export disable when grid draw is high
- SOC-based trim logic — trims to 82% when battery exceeds 85%, and trims after a 1-hour hold on full-charge days
- Weekly best full-charge day selection scored from weather forecast
- Monitor mode — observe decisions without writing to the Sunsynk API
- Manual control buttons for all actions
- Auto-generated Lovelace dashboard with adaptive learning, history graphs, and tuning assist sections *(dashboard adaptive learning section and 48h graphs are beta)*
- Optional push notifications via any HA notify service

## Requirements

Install and configure these first.

### 1. SolarSynkV3

Required for inverter sensor entities.

GitHub repository:
`https://github.com/martinville/solarsynkv3`

This integration provides entities such as:

- `sensor.solarsynkv3_<inverter_serial>_battery_soc`
- `sensor.solarsynkv3_<inverter_serial>_grid_pac`
- `sensor.solarsynkv3_<inverter_serial>_pv_mppt0_power`
- `sensor.solarsynkv3_<inverter_serial>_pv_mppt1_power`
- `sensor.solarsynkv3_<inverter_serial>_pv_etoday` (used for actual solar logging)

Setup validation checks that `battery_soc` and `grid_pac` entities exist before the integration will save.

### 2. Forecast.Solar

Required for solar forecast input.

This integration should provide:

- `sensor.energy_production_today`

### 3. Weather entity

Required for full-charge day scoring.

Example:

- `weather.forecast_home`

### 4. Sunsynk Power Flow Card (required for dashboard)

The optimizer dashboard uses the custom power flow card:

https://github.com/slipx06/sunsynk-power-flow-card

## Installation via HACS

### Step 1: Add the custom repository

In Home Assistant:

1. Open **HACS**
2. Go to **Integrations**
3. Open the menu in the top right
4. Select **Custom repositories**
5. Add this repository URL:

```text
https://github.com/dharv79/Sunsynk_HA_Optimizer
```

6. Set the category to:

```text
Integration
```

7. Click **Add**
8. Search for:

```text
Sunsynk Optimizer
```

9. Click **Download**

## Step 2: Add the integration and configure it

After download:

1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for:

```text
Sunsynk Optimizer
```

4. Open the integration setup
5. Enter the requested values

### Configuration fields

#### Sunsynk username / password

Your Sunsynk cloud account credentials. Login is validated during setup.

#### Plant ID

The Sunsynk API plant/station ID used for all API writes. Numeric.

Example:

```text
111111
```

#### Inverter Serial

The SolarSynkV3 inverter serial used to build HA sensor entity IDs. Alphanumeric.

Example:

```text
1111111111
```

#### Forecast sensor

Entity ID of the solar energy forecast sensor.

```text
sensor.energy_production_today
```

#### Weather entity

Entity ID of a daily weather forecast entity.

```text
weather.forecast_home
```

#### Notify service

HA notify service to use for plan and action notifications.

```text
notify.notify
```

or if using Slack:

```text
notify.slack
```

#### Notify target (optional)

Target channel or device for the notify service.

```text
#slackhomenotifications
```

#### Operation mode

- `auto` — full optimizer behaviour, API writes enabled
- `monitor` — all logic runs and state is updated, but no API calls are made

#### Export disable threshold (W)

Grid/load power above this wattage between 16:00–19:00 disables Flux 2 export by setting target SOC to 100%.

Default: `1500`

#### Default full-charge day

Fallback full-charge day used before the weekly scoring has run.

Default: `Wednesday`

#### Currency / Invest

Used internally for tariff tracking. Defaults are `366` and `9400` respectively.

## Important ID mapping

These two values are different and must not be swapped.

| Use | Field |
|---|---|
| SolarSynkV3 sensor entity suffix | `inverter_serial` |
| Sunsynk API writes | `plant_id` |

Example:

- SolarSynkV3 entities use `1111111111`
- Sunsynk API writes use `111111`

## Options flow (reconfiguring)

After initial setup, open the integration and select **Configure** to re-enter settings. The options flow is multi-step:

1. **init** — main settings (all fields above except credentials)
2. **charges_1** — import/export tariff rows 1–4
3. **charges_2** — import/export tariff rows 5–8
4. **flux** — baseline Flux 1 (import) and Flux 2 (export) window times and target SOC

## Operation modes

### auto

All optimizer behaviour is active. Import plans are calculated and pushed to the Sunsynk API via Flux override. Flux 2 export control and SOC trimming are active.

### monitor

All logic paths run and state entities are updated, but no API calls are made. Useful for observing what the optimizer would do without affecting the inverter. The `binary_sensor.monitor_only_mode` entity turns on when this mode is active.

## Automation behaviour

### Import plan (01:55 daily)

Runs each night at 01:55 to set the overnight charging window (Flux 1).

Before calculating targets, three adaptive corrections are fetched from historical data:

1. **Forecast correction** — scales the raw forecast by the actual/forecast ratio from the last 30 days. Active after 7+ paired days.
2. **Overnight drain compensation** — extra % added to target SOC to cover battery drain between charge end and 06:00. Active after 5+ mornings.
3. **Evening SOC nudge** — shifts target ±5% if the battery consistently ends the day too full or too empty. Active after 5+ matching days per forecast band.

SOC target is calculated as:

| Condition | Target SOC |
|---|---|
| Full-charge day | 100% |
| Low solar (< 7 kWh), winter-like | 100% |
| Low solar (< 7 kWh), other | 95% |
| Winter-like (≤ 5 kWh) | 95% |
| Summer-like (≥ 10 kWh) | 80% |
| Shoulder | 85% |

Adaptive corrections are applied on top (clamped 50–100%).

Import window end time starts at 04:00 and is adjusted based on current SOC and forecast band, then clamped to 02:15–05:00. If forecast is below 7 kWh the window is always extended to 05:00.

### Morning state capture (06:00 daily)

Records battery SOC and combined PV MPPT0+MPPT1 power at 06:00 to measure overnight battery drain for adaptive correction.

### Full-charge day selection (Sunday at 18:00)

Scores Monday–Friday from the daily weather forecast. Scoring:

- Base: `100 − cloud_coverage − (rain_probability × 0.7)`
- Condition bonus: +25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow
- Temperature: +3 if ≥ 18 °C, −3 if ≤ 5 °C
- Day-of-week penalty: Thursday −5, Friday −15

The highest-scoring day is set as the full-charge day for the coming week.

### Flux 2 check (every 30 minutes, and on SOC change)

Controls Flux 2 (export window) based on real-time conditions:

- If grid/load power exceeds the export disable threshold between 16:00–19:00 → sets Flux 2 target SOC to 100% (disables export)
- If SOC exceeds 85% on a non-full-charge day → trims to 82% for a 45-minute window
- On the full-charge day, when SOC reaches 99.5%, schedules a 1-hour hold then trims to 82%

### Evening actuals capture (22:00 daily)

Records end-of-day battery SOC and actual PV generation (`pv_etoday`) for adaptive learning.

## Adaptive learning *(beta — v1.0.8b3 only)*

> This feature is not present in the stable v1.0.7 release. On a fresh install from the stable channel the optimizer behaviour is unchanged from v1.0.7.

History is written to JSONL files at:

```text
{ha_config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl
```

Four record types are logged:

| Type | When | Contents |
|---|---|---|
| `import_plan` | 01:55 | SOC, forecast, corrections, target SOC, import window |
| `morning_state` | 06:00 | Battery SOC and PV power at dawn |
| `day_actuals` | 22:00 | Evening SOC and actual solar kWh |
| `full_charge_day` | Sunday 18:00 | Scores and chosen day |

Files older than 13 months are pruned automatically on HA startup.

All three corrections return neutral values (no effect) until enough days have accumulated, so the optimizer behaves identically to v1.0.7 on a fresh install.

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.selected_full_charge_day` | Currently selected full-charge day. Attributes include all day scores. |
| `sensor.import_plan_end` | Current import window summary (e.g. `02:00→04:00 target 85%`). Attributes expose the full plan including all adaptive corrections. |
| `sensor.flux_2_action` | Last Flux 2 action taken. Attributes expose the full action dict. |
| `sensor.next_import_window` | Import window string for the upcoming night. |
| `sensor.current_soc_target` | Target SOC set by the last import plan. |
| `sensor.operation_mode` | Current mode: `auto` or `monitor`. |
| `sensor.last_error` | Last error message, or `OK`. Attributes include last API result and notification status. |
| `sensor.last_updated` | ISO timestamp of the last state update. |

### Binary sensors

| Entity | Description |
|---|---|
| `binary_sensor.evening_export_disabled` | On when Flux 2 export has been disabled by a high grid draw event. |
| `binary_sensor.monitor_only_mode` | On when operation mode is `monitor`. |

### Buttons

| Button | Action |
|---|---|
| Run import plan | Manually trigger the overnight import plan calculation and API push |
| Run Flux 2 check | Manually trigger the Flux 2 export/trim check |
| Run choose best day | Manually trigger the full-charge day scoring |
| Reset to baseline | Push the baseline Flux settings from config and clear export-disabled state |
| Install dashboard | Regenerate the Lovelace dashboard file |

## Step 3: Install the dashboard

Once the integration is configured:

1. Open **Settings → Devices & Services**
2. Open the **Sunsynk Optimizer** integration
3. Go to its **Entities**
4. Find and press:

```text
Install dashboard
```

This generates a dashboard YAML file in the HA config directory, with all SolarSynkV3 entity IDs built automatically from your configured inverter serial.

## Step 4: Add the Lovelace dashboard config

After pressing **Install dashboard**, a persistent notification shows the generated filename.

Add the snippet from the notification to your `configuration.yaml`:

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

## Step 5: Restart HA

Restart HA after saving `configuration.yaml`.

Once back up, open the dashboard from the sidebar.

## Dashboard sections

| Section | Contents |
|---|---|
| Power flow | Live Sunsynk power flow card |
| KPI summary | Daily energy totals and optimizer KPIs (SOC target, import window, mode) |
| Adaptive learning | Active correction factors and a plain-English explanation of what the system has learned |
| Optimizer status | All optimizer state entities |
| Manual controls | Buttons for all actions plus config notes |
| Forecast and live values | Weather, forecast sensor, key inverter values, 48-hour SOC/grid and PV history graphs |
| Tuning assist | Seasonal guidance and all relevant input values |
| Event detail | Last import plan, last Flux 2 action, Why this plan attributes, logbook |

## Updating the dashboard

If you change the integration configuration or the dashboard layout changes in a new release:

1. Press **Install dashboard** again
2. The dashboard file is rebuilt immediately — no manual editing needed

## Notifications

Notifications use the configured notify service and are sent for:

- Import plan calculated (01:55)
- Full-charge day updated (Sunday 18:00)
- Evening export disabled
- SOC trim to 82%
- Full-charge day trim to 82%
- Baseline restored

If your notify platform needs a target, set `notify_target` in configuration.

## Troubleshooting

### Integration will not save during setup

The setup validates that `sensor.solarsynkv3_{inverter_serial}_battery_soc` and `sensor.solarsynkv3_{inverter_serial}_grid_pac` exist. Ensure SolarSynkV3 is installed and has loaded at least one data cycle before adding this integration.

### Dashboard installs with wrong sensors

Check that:

- `inverter_serial` is the SolarSynkV3 serial (alphanumeric)
- `plant_id` is the Sunsynk API plant ID (numeric)

Then press **Install dashboard** again.

### Notifications do not send

Check:

- the notify service exists in HA
- the notify target is correct if required
- the integration `Last error` entity for details

### Import plan fails

Check:

- `plant_id` is correct
- Sunsynk credentials are correct
- the integration can reach the Sunsynk API (check `Last error` entity)

### Adaptive corrections not activating

Corrections are inactive until enough days of history exist (7+ for forecast correction, 5+ for drain and evening nudge). This is expected on a fresh install.

### Spook shows unknown dashboard entities

Press **Install dashboard** again after correcting config values and remove any older static dashboards.

## Updating

When updating through HACS:

1. Download the update
2. Reload the integration via **Settings → Devices & Services** if needed
3. Press **Install dashboard** again if the dashboard structure has changed

## Notes

This integration requires SolarSynkV3 and Forecast.Solar to already be installed and working. It does not replace those integrations; it builds optimisation logic on top of them.

The `cryptography` library used for Sunsynk API login is bundled with Home Assistant and does not need to be installed separately.
