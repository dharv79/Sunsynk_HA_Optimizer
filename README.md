# Sunsynk HA Optimizer

Smart Home Assistant integration to optimise Sunsynk inverter charging and export behaviour using solar forecast, battery SOC, time-of-use windows, and automated Flux control.

## Features

- Smart overnight import planning
- Dynamic Flux 2 export control
- Weekly best full-charge day selection
- Forecast-driven optimisation
- SOC-based trimming logic
- Manual control buttons
- Generated dashboard install
- Optional notifications

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

### 2. Forecast.Solar

Required for solar forecast input.

This integration should provide:

- `sensor.energy_production_today`

### 3. Weather entity

Recommended for forecast-based logic.

Example:

- `weather.forecast_home`

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

#### Plant ID

This is the Sunsynk API plant ID used for API writes.

Example:

```text
111111
```

#### Inverter Serial

This is the SolarSynkV3 inverter serial used in Home Assistant sensor entity IDs.

Example:

```text
1111111111
```

#### Forecast sensor

Example:

```text
sensor.energy_production_today
```

#### Weather entity

Example:

```text
weather.forecast_home
```

#### Notify service

Examples:

```text
notify.notify
```

or if slack name as per your config

```text
notify.slack
```

#### Optional notify target

Example for Slack (depends what you channel name is):

```text
#slackhomenotifications
```

## Important ID mapping

These two values are different and must not be swapped.

| Use | Field |
|---|---|
| SolarSynkV3 sensor entity suffix | `inverter_serial` |
| Sunsynk API writes | `plant_id` |

Example:

- SolarSynkV3 entities use `1111111111`
- Sunsynk API writes use `111111`

## Step 3: Install the dashboard

Once the integration is configured:

1. Open **Settings → Devices & Services**
2. Open the **Sunsynk Optimizer** integration
3. Go to its **Entities**
4. Find and run:

```text
Install dashboard
```

This generates a dashboard file from your saved config so the SolarSynkV3 sensor entity IDs are built automatically from your configured inverter serial.

## Step 4: Add the Lovelace dashboard config

After pressing **Install dashboard**, a persistent notification will show the generated filename.

Add the snippet shown in the notification to your `configuration.yaml`.

It will look like this:

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

Replace `<entry_id>` with the generated file name shown in the notification if needed.

## Step 5: Reload YAML / Lovelace and use the dashboard

After saving `configuration.yaml`, apply the change in Home Assistant.

Then open the dashboard from the sidebar.

## Updating the dashboard

If you change the integration configuration later, or the dashboard layout is updated:

1. Run **Install dashboard** again
2. The generated dashboard file will be rebuilt

No manual dashboard editing should be needed.

## Manual controls

The integration provides these buttons:

- Run choose best day
- Run import plan
- Run Flux 2 check
- Reset to baseline
- Install dashboard

## Automation behaviour

### Run import plan

Runs automatically each day at approximately:

```text
01:55
```

It uses:

- battery SOC
- selected full-charge day
- solar forecast sensor

It updates the import plan and baseline Flux settings.

### Full-charge day selection

Runs weekly on Sunday evening.

It scores Monday to Friday using the weather forecast and selects the best full-charge day.

### Flux 2 check

Runs periodically to control export behaviour using:

- battery SOC
- grid/load power
- time window

## Forecast sensor used

The optimiser uses the configured solar forecast sensor.

Default:

```text
sensor.energy_production_today
```

## Notifications

Notifications use the configured notify service.

Examples:

- `notify.notify`
- `notify.slack`

If your notify platform needs a target, set `notify_target` as well.

Example:

```text
#homenotifications
```

## Troubleshooting

### Dashboard installs with wrong sensors

Check that:

- `inverter_serial` is the SolarSynkV3 serial
- `plant_id` is the Sunsynk API plant ID

Then run **Install dashboard** again.

### Notifications do not send

Check:

- the notify service exists
- the notify target is correct if required
- the integration `Last error` entity for details

### Import plan fails

Check:

- `plant_id` is correct
- Sunsynk credentials are correct
- the integration can reach the Sunsynk API

### Spook shows unknown dashboard entities

Run **Install dashboard** again after correcting config values and remove older static dashboards if present.

## Updating

When updating through HACS:

1. Download the update
2. If needed, reload the integration
3. Run **Install dashboard** again if the dashboard structure has changed

## Notes

This integration expects SolarSynkV3 and Forecast.Solar to already be installed and working.

It does not replace those integrations; it builds optimisation logic on top of them.
