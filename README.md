# Sunsynk HA Optimizer

Smart Home Assistant integration to optimise Sunsynk inverter charging and export behaviour using solar forecast, battery SOC, time-of-use windows, and automated Flux control.

Current release: **v1.0.8**

## Features

- Smart overnight import planning driven by solar forecast and battery SOC
- Adaptive learning — self-corrects forecast bias, overnight battery drain, and evening SOC outcomes over time
- Battery temperature deration — reduces charge rate automatically in cold weather
- Weekend consumption mode — uses a higher consumption figure on Saturdays and Sundays
- Dynamic Flux 2 export control with evening export disable when grid draw is high
- SOC-based trim logic — trims to 82% when battery exceeds 85%, and trims after a 1-hour hold on full-charge days
- Weekly best full-charge day selection scored from weather forecast
- Monitor mode — observe decisions without writing to the Sunsynk API
- Auto-generated Lovelace dashboard with adaptive learning, history graphs, and tuning assist sections
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

#### Notify target (optional)

Target channel or device for the notify service.

#### Data report target (optional)

A secondary notify target that receives a full JSON debug report at 22:00 each day, containing the import plan, morning state, and day actuals. Useful for logging to a Slack channel or similar.

#### Operation mode

- `auto` — full optimizer behaviour, API writes enabled
- `monitor` — all logic runs and state is updated, but no API calls are made

#### Export disable threshold (W)

Grid/load power above this wattage between 16:00–19:00 disables Flux 2 export by setting target SOC to 100%.

Default: `1500`

#### Average consumption (weekday / weekend)

Average home load in kW, used for the solar bridge target calculation. Separate values for weekdays and weekends.

Default weekday: `0.75` kW  
Default weekend: `0.90` kW

#### Default full-charge day

Fallback full-charge day used before the weekly scoring has run.

Default: `Wednesday`

## Important ID mapping

These two values are different and must not be swapped.

| Use | Field |
|---|---|
| SolarSynkV3 sensor entity suffix | `inverter_serial` |
| Sunsynk API writes | `plant_id` |

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

Before calculating targets, four adaptive corrections are fetched from historical data:

1. **Forecast correction** — scales the raw forecast by the *median* actual/forecast ratio from the last 30 days (median so one freak day can't skew it). Active after 7+ paired days.
2. **Overnight drain compensation** — extra % added to target SOC to cover battery drain between charge end and 06:00. Uses the 75th percentile of recent drain values so appliance-heavy nights are covered, measured only on nights where the battery actually charged (so a night that started already-full isn't mistaken for drain). Active after 5+ qualifying mornings (returns 15% fallback until then).
3. **Evening SOC nudge** — shifts target ±5% if the battery consistently ends the day too full or too empty. Active after 5+ matching days per forecast band.
4. **Effective charge rate** — calibrated kW rate from historical charging sessions, used to size the import window precisely. When recent nights are too short to calibrate (common in summer), the last learned rate is reused rather than reverting to the configured nameplate rate.

Low-solar decisions use the **pessimistic** of the raw and corrected forecast, so a genuinely poor day isn't inflated above the low-solar threshold by the learned correction (which is derived mostly from good days).

SOC target is calculated as:

| Condition | Target SOC |
|---|---|
| Full-charge day (solar bridge) | 20% + hours-to-solar × consumption / capacity |
| Low solar (< 7 kWh), winter-like | 100% |
| Low solar (< 7 kWh), other | 95% |
| Solar bridge (normal) | 20% + hours-to-solar × consumption / capacity |
| Band fallback, summer-like | 80% |
| Band fallback, shoulder | 85% |
| Band fallback, winter-like | 95% |

Adaptive corrections are applied on top (clamped 30–100%).

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

Records end-of-day battery SOC and actual PV generation (`pv_etoday`) for adaptive learning. If a data report target is configured, posts the full day JSON to that notify target.

## Adaptive learning

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

All corrections return neutral values until enough days have accumulated — the optimizer behaves conservatively on a fresh install.

## Entities

### Sensors

| Entity | Description |
|---|---|
| `sensor.selected_full_charge_day` | Currently selected full-charge day. Attributes include all day scores. |
| `sensor.import_plan_end` | Current import window summary. Attributes expose the full plan including all adaptive corrections. |
| `sensor.flux_2_action` | Last Flux 2 action taken. Attributes expose the full action dict. |
| `sensor.next_import_window` | Import window string for the upcoming night. |
| `sensor.current_soc_target` | Target SOC set by the last import plan. |
| `sensor.operation_mode` | Current mode: `auto` or `monitor`. |
| `sensor.last_error` | Last error message, or `OK`. |
| `sensor.last_updated` | ISO timestamp of the last state update. |

### Binary sensors

| Entity | Description |
|---|---|
| `binary_sensor.evening_export_disabled` | On when Flux 2 export has been disabled by a high grid draw event. |
| `binary_sensor.monitor_only_mode` | On when operation mode is `monitor`. |

### Buttons

| Button | Action |
|---|---|
| Test plan (dry run) | Recomputes the full import plan and sends the complete JSON to the app notification — no inverter push, safe to use any time |
| Reset to baseline | Push the baseline Flux settings from config and clear export-disabled state |
| Update dashboard | Regenerate the Lovelace dashboard YAML file |

## Step 3: Install the dashboard

Once the integration is configured:

1. Open **Settings → Devices & Services**
2. Open the **Sunsynk Optimizer** integration
3. Go to its **Entities**
4. Find and press **Update dashboard**

This generates a dashboard YAML file in the HA config directory, with all SolarSynkV3 entity IDs built automatically from your configured inverter serial.

## Step 4: Add the Lovelace dashboard config

After pressing **Update dashboard**, a persistent notification shows the generated filename.

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

Restart HA after saving `configuration.yaml`. Once back up, open the dashboard from the sidebar.

## Dashboard sections

| Section | Contents |
|---|---|
| Power flow | Live Sunsynk power flow card |
| KPI summary | Daily energy totals and optimizer KPIs |
| Adaptive learning | Active correction factors and a plain-English explanation of what the system has learned |
| Optimizer status | All optimizer state entities |
| Manual controls | Test plan, Reset baseline, Update dashboard buttons |
| Forecast and live values | Weather, forecast sensor, key inverter values, 48-hour SOC/grid and PV history graphs |
| Tuning assist | Seasonal guidance and all relevant input values |
| Event detail | Last import plan, last Flux 2 action, logbook |

## Updating the dashboard

If you change the integration configuration or the dashboard layout changes in a new release, press **Update dashboard** again. The dashboard file is rebuilt immediately — no manual editing needed.

## Notifications

Notifications use the configured notify service and are sent for:

- Import plan calculated (01:55)
- Full-charge day updated (Sunday 18:00)
- Evening export disabled
- SOC trim to 82%
- Full-charge day trim to 82%
- Baseline restored

## Troubleshooting

### Integration will not save during setup

The setup validates that `sensor.solarsynkv3_{inverter_serial}_battery_soc` and `sensor.solarsynkv3_{inverter_serial}_grid_pac` exist. Ensure SolarSynkV3 is installed and has loaded at least one data cycle before adding this integration.

### Dashboard installs with wrong sensors

Check that `inverter_serial` is the SolarSynkV3 serial (alphanumeric) and `plant_id` is the Sunsynk API plant ID (numeric), then press **Update dashboard** again.

### Notifications do not send

Check the notify service exists in HA, the notify target is correct if required, and the integration **Last error** entity for details.

### Import plan fails

Check that `plant_id` is correct, Sunsynk credentials are correct, and the integration can reach the Sunsynk API (check **Last error** entity).

### Adaptive corrections not activating

Corrections are inactive until enough days of history exist (7+ for forecast correction, 5+ for drain and evening nudge). This is expected on a fresh install. The overnight drain adjustment returns a 15% safety buffer until the 5-day threshold is met.

### HACS shows no information

Press **Update information** from the HACS context menu for this integration to force a refresh of the repository info.

## Updating

When updating through HACS:

1. Download the update
2. Reload the integration via **Settings → Devices & Services**
3. Press **Update dashboard** if the dashboard structure has changed in the release notes

## Notes

This integration requires SolarSynkV3 and Forecast.Solar to already be installed and working. It does not replace those integrations; it builds optimisation logic on top of them.

The `cryptography` library used for Sunsynk API login is bundled with Home Assistant and does not need to be installed separately.
