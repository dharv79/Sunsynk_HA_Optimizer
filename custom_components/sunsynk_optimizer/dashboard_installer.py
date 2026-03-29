"""Dynamic dashboard installer for Sunsynk Optimizer."""

from __future__ import annotations

from pathlib import Path
import json

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PLANT_ID, CONF_INVERTER_SERIAL
from .flux_helpers import merge_entry_data


def _build_dashboard(config: dict) -> dict:
    solar_entity_suffix = str(config[CONF_INVERTER_SERIAL]).strip()
    api_plant_id = str(config[CONF_PLANT_ID]).strip()
    forecast_sensor = config.get("solar_forecast_sensor", "sensor.energy_production_today")
    weather_entity = config.get("weather_entity", "weather.forecast_home")

    def s(name: str) -> str:
        return f"sensor.solarsynkv3_{solar_entity_suffix}_{name}"

    seasonal_guidance = f"""{{% set forecast = states('{forecast_sensor}') | float(0) %}}
{{% if forecast >= 10 %}}
**Season:** Summer-like  

Suggestion: bias toward **lower overnight import**, shorter import windows, and more trust in solar recovery.
{{% elif forecast <= 5 %}}
**Season:** Winter-like  

Suggestion: bias toward **higher overnight SOC targets**, longer import windows, and less reliance on daytime solar.
{{% else %}}
**Season:** Shoulder / mixed day  

Suggestion: use **mid SOC targets** and let forecast drive import end times more aggressively.
{{% endif %}}"""

    return {
        "title": "Sunsynk Optimizer Dashboard",
        "views": [
            {
                "title": "Sunsynk Optimizer",
                "path": "sunsynk-optimizer",
                "icon": "mdi:battery-heart-variant",
                "type": "sections",
                "max_columns": 4,
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Power flow", "heading_style": "title"},
                            {
                                "type": "custom:sunsynk-power-flow-card",
                                "cardstyle": "lite",
                                "wide": False,
                                "large_font": False,
                                "show_solar": True,
                                "show_battery": True,
                                "show_grid": True,
                                "center_no_grid": True,
                                "decimal_places": 1,
                                "decimal_places_energy": 1,
                                "dynamic_line_width": True,
                                "max_line_width": 4,
                                "min_line_width": 1,
                                "inverter": {
                                    "modern": True,
                                    "autarky": "no",
                                    "model": "sunsynk",
                                    "auto_scale": False,
                                },
                                "battery": {
                                    "shutdown_soc": 20,
                                    "show_daily": False,
                                    "count": 1,
                                    "dynamic_colour": True,
                                    "linear_gradient": True,
                                    "show_remaining_energy": False,
                                    "remaining_energy_to_shutdown": False,
                                    "animate": True,
                                    "auto_scale": False,
                                },
                                "solar": {
                                    "show_daily": False,
                                    "mppts": 2,
                                    "dynamic_colour": True,
                                    "pv1_name": "Ext",
                                    "pv2_name": "Main",
                                    "auto_scale": False,
                                },
                                "load": {
                                    "show_daily": False,
                                    "dynamic_colour": True,
                                    "dynamic_icon": True,
                                    "invert_load": False,
                                    "show_aux": False,
                                    "essential_name": "Home",
                                },
                                "grid": {
                                    "show_daily_buy": False,
                                    "show_daily_sell": False,
                                    "show_nonessential": False,
                                    "grid_name": "Octopus",
                                },
                                "entities": {
                                    "inverter_status_59": s("runstatus"),
                                    "inverter_power_175": s("load_total_power"),
                                    "battery_soc_184": s("battery_soc"),
                                    "battery_power_190": s("battery_power"),
                                    "battery_voltage_183": s("battery_voltage"),
                                    "battery_current_191": s("battery_current"),
                                    "pv1_power_186": s("pv_mppt0_power"),
                                    "pv2_power_187": s("pv_mppt1_power"),
                                    "grid_power_169": s("grid_pac"),
                                },
                            },
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "KPI summary", "heading_style": "title"},
                            {
                                "type": "glance",
                                "title": "Energy summary",
                                "columns": 4,
                                "entities": [
                                    {"entity": forecast_sensor, "name": "Solar forecast"},
                                    {"entity": s("grid_etoday_from"), "name": "Grid import"},
                                    {"entity": s("battery_etoday_discharge"), "name": "Battery used"},
                                    {"entity": s("load_daily_used"), "name": "Load today"},
                                ],
                            },
                            {
                                "type": "glance",
                                "title": "Optimizer KPI",
                                "columns": 4,
                                "entities": [
                                    {"entity": "sensor.selected_full_charge_day", "name": "Full day"},
                                    {"entity": "sensor.current_soc_target", "name": "SOC target"},
                                    {"entity": "sensor.next_import_window", "name": "Import window"},
                                    {"entity": "sensor.operation_mode", "name": "Mode"},
                                ],
                            },
                            {
                                "type": "glance",
                                "title": "Flags",
                                "columns": 2,
                                "entities": [
                                    {"entity": "binary_sensor.evening_export_disabled", "name": "Export disabled"},
                                    {"entity": "binary_sensor.monitor_only_mode", "name": "Monitor only"},
                                ],
                            },
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Optimizer status", "heading_style": "title"},
                            {
                                "type": "entities",
                                "title": "Optimizer settings",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": "sensor.selected_full_charge_day"},
                                    {"entity": "sensor.operation_mode"},
                                    {"entity": "sensor.current_soc_target"},
                                    {"entity": "sensor.next_import_window"},
                                    {"entity": "sensor.import_plan_end"},
                                    {"entity": "sensor.flux_2_action"},
                                    {"entity": "sensor.last_updated"},
                                    {"entity": "sensor.last_error"},
                                ],
                            }
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Manual controls", "heading_style": "title"},
                            {
                                "type": "grid",
                                "columns": 2,
                                "square": False,
                                "cards": [
                                    {
                                        "type": "button",
                                        "name": "Run import plan",
                                        "icon": "mdi:clock-start",
                                        "tap_action": {
                                            "action": "call-service",
                                            "service": "button.press",
                                            "target": {"entity_id": "button.run_import_plan"},
                                        },
                                    },
                                    {
                                        "type": "button",
                                        "name": "Run Flux 2 check",
                                        "icon": "mdi:transmission-tower-export",
                                        "tap_action": {
                                            "action": "call-service",
                                            "service": "button.press",
                                            "target": {"entity_id": "button.run_flux_2_check"},
                                        },
                                    },
                                    {
                                        "type": "button",
                                        "name": "Choose best day",
                                        "icon": "mdi:calendar-star",
                                        "tap_action": {
                                            "action": "call-service",
                                            "service": "button.press",
                                            "target": {"entity_id": "button.run_choose_best_day"},
                                        },
                                    },
                                    {
                                        "type": "button",
                                        "name": "Reset baseline",
                                        "icon": "mdi:restore",
                                        "tap_action": {
                                            "action": "call-service",
                                            "service": "button.press",
                                            "target": {"entity_id": "button.reset_to_baseline"},
                                        },
                                    },
                                    {
                                        "type": "button",
                                        "name": "Install dashboard",
                                        "icon": "mdi:view-dashboard-edit",
                                        "tap_action": {
                                            "action": "call-service",
                                            "service": "button.press",
                                            "target": {"entity_id": "button.install_dashboard"},
                                        },
                                    },
                                ],
                            },
                            {
                                "type": "markdown",
                                "title": "Notes",
                                "content": (
                                    f"**Plant ID:** {api_plant_id}  \\n"
                                    f"**Inverter S/N:** {solar_entity_suffix}  \\n"
                                    f"**Weather entity:** {weather_entity}  \\n"
                                    f"**Forecast sensor:** {forecast_sensor}\\n\\n"
                                    "Manual controls call the Sunsynk Optimizer buttons directly.  \\n"
                                    "Status cards show the latest calculated import window, Flux 2 action, and mode."
                                ),
                            },
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Forecast and live values", "heading_style": "title"},
                            {
                                "type": "entities",
                                "title": "Forecast and weather",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": weather_entity, "name": "Weather"},
                                    {"entity": forecast_sensor, "name": "Solar forecast today"},
                                ],
                            },
                            {
                                "type": "entities",
                                "title": "Key inverter values",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": s("battery_soc"), "name": "Battery SOC"},
                                    {"entity": s("grid_pac"), "name": "Grid power"},
                                    {"entity": s("pv_mppt0_power"), "name": "PV MPPT0"},
                                    {"entity": s("pv_mppt1_power"), "name": "PV MPPT1"},
                                    {"entity": s("load_total_power"), "name": "Load power"},
                                ],
                            },
                            {
                                "type": "history-graph",
                                "title": "Battery SOC and grid",
                                "hours_to_show": 24,
                                "entities": [
                                    {"entity": s("battery_soc")},
                                    {"entity": s("grid_pac")},
                                ],
                            },
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Tuning assist", "heading_style": "title"},
                            {
                                "type": "markdown",
                                "title": "Seasonal guidance",
                                "content": seasonal_guidance,
                            },
                            {
                                "type": "entities",
                                "title": "Inputs that matter for tuning",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": forecast_sensor, "name": "Forecast today"},
                                    {"entity": s("battery_soc"), "name": "Current SOC"},
                                    {"entity": "sensor.current_soc_target", "name": "Current target SOC"},
                                    {"entity": "sensor.next_import_window", "name": "Planned import window"},
                                    {"entity": "sensor.selected_full_charge_day", "name": "Selected full-charge day"},
                                    {"entity": s("grid_etoday_from"), "name": "Grid import today"},
                                    {"entity": s("grid_etoday_to"), "name": "Grid export today"},
                                    {"entity": s("battery_etoday_charge"), "name": "Battery charge today"},
                                    {"entity": s("battery_etoday_discharge"), "name": "Battery discharge today"},
                                    {"entity": s("pv_etoday"), "name": "PV today"},
                                    {"entity": s("load_daily_used"), "name": "Load today"},
                                ],
                            },
                        ],
                    },
                    {
                        "type": "grid",
                        "cards": [
                            {"type": "heading", "heading": "Event detail", "heading_style": "title"},
                            {
                                "type": "entities",
                                "title": "Last results",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": "sensor.import_plan_end", "name": "Last import plan"},
                                    {"entity": "sensor.flux_2_action", "name": "Last Flux 2 action"},
                                    {"entity": "sensor.last_error", "name": "Last error / API result"},
                                ],
                            },
                            {
                                "type": "entities",
                                "title": "Why this plan",
                                "show_header_toggle": False,
                                "entities": [
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "today", "name": "Today"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "selected_full_charge_day", "name": "Chosen full-charge day"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "is_full_day", "name": "Is full-charge day"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "soc", "name": "SOC at planning time"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "solar_forecast_kwh", "name": "Solar forecast used"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "flux1_end", "name": "Planned import end"},
                                    {"entity": "sensor.import_plan_end", "type": "attribute", "attribute": "target_soc", "name": "Planned target SOC"},
                                ],
                            },
                            {
                                "type": "logbook",
                                "title": "Optimizer activity",
                                "hours_to_show": 24,
                                "entities": [
                                    "sensor.selected_full_charge_day",
                                    "sensor.import_plan_end",
                                    "sensor.flux_2_action",
                                    "sensor.last_error",
                                    "binary_sensor.evening_export_disabled",
                                    "binary_sensor.monitor_only_mode",
                                ],
                            },
                        ],
                    },
                ],
            }
        ],
    }


async def async_install_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Generate dashboard YAML from config-entry values and notify the user."""
    config = merge_entry_data(dict(entry.data), dict(entry.options))
    dashboard = _build_dashboard(config)

    config_dir = Path(hass.config.config_dir)
    filename = f"sunsynk_optimizer_{entry.entry_id}.yaml"
    dst = config_dir / filename
    dst.write_text(json.dumps(dashboard, indent=2), encoding="utf-8")

    snippet = f"""Add this to configuration.yaml and restart Home Assistant:

lovelace:
  dashboards:
    sunsynk-optimizer:
      mode: yaml
      filename: {filename}
      title: Sunsynk Optimizer
      icon: mdi:battery-heart-variant
      show_in_sidebar: true

Dashboard file written to:
{dst}
"""

    persistent_notification.async_create(
        hass,
        snippet,
        title="Sunsynk Optimizer dashboard installed",
        notification_id="sunsynk_optimizer_dashboard_install",
    )
