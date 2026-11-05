# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Dynamic dashboard installer for Sunsynk Optimizer.

Six views built from Home Assistant's own sections-view cards (heading badges,
tiles, gauges, markdown, statistics), plus two HACS cards:
custom:sunsynk-power-flow-card and custom:apexcharts-card (the attribute charts).
"""

from __future__ import annotations

import logging
from pathlib import Path
import json
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_AVG_CONSUMPTION_KW, CONF_PLANT_ID, CONF_INVERTER_SERIAL, DEFAULT_AVG_CONSUMPTION_KW
from .flux_helpers import merge_entry_data

_LOGGER = logging.getLogger(__name__)

# One palette across tiles and charts: solar, battery, import, export, load, shadow.
_SOLAR, _BATTERY, _IMPORT, _EXPORT, _LOAD, _SHADOW = "#F5B300", "#22C55E", "#EF4444", "#14B8A6", "#3B82F6", "#8B5CF6"

# Jinja helpers prepended to markdown cards: a(entity, attr, unit) prints an attribute
# or "—"; gbp(entity, attr) prints money as £1.23 / -£1.23 or "—". Missing is never 0.
_MACROS = (
    "{%- macro a(e, k, u='') -%}{%- set x = state_attr(e, k) -%}"
    "{%- if x is none or x == '' -%}—{%- elif x is boolean -%}{{ 'Yes' if x else 'No' }}"
    "{%- else -%}{{ x }}{{ u }}{%- endif -%}{%- endmacro -%}"
    "{%- macro gbp(e, k) -%}{%- set x = state_attr(e, k) -%}"
    "{%- if x is number and x is not boolean -%}{{ '-' if x < 0 else '' }}£{{ '%.2f' | format(x | abs) }}"
    "{%- else -%}—{%- endif -%}{%- endmacro -%}\n"
)

_PLAN = "sensor.import_plan_end"

# The "Why this plan?" table: (group, ((attribute, label, unit suffix), ...)).
_PLAN_ROWS = (
    ("Inputs", (
        ("today", "Day", ""),
        ("soc", "SOC at planning time", "%"),
        ("raw_forecast_kwh", "Raw solar forecast", " kWh"),
        ("forecast_correction_factor", "Forecast correction", " ×"),
        ("solar_forecast_kwh", "Adjusted forecast", " kWh"),
        ("low_solar_forecast_kwh", "Low-solar value used", " kWh"),
        ("forecast_band", "Forecast band", ""),
        ("avg_consumption_kw", "Consumption rate used", " kW"),
        ("is_weekend", "Weekend mode", ""),
        ("away", "Away calibration", ""),
    )),
    ("Decision", (
        ("selected_full_charge_day", "Chosen full-charge day", ""),
        ("is_full_day", "Full-charge day", ""),
        ("target_soc_reason", "Target reason", ""),
        ("logic_branch", "Logic branch", ""),
        ("overnight_drain_adjustment", "Drain compensation", "%"),
        ("soc_adjustment", "Evening SOC nudge", "%"),
        ("target_soc", "Final target", "%"),
        ("flux1_end", "Import ends", ""),
    )),
    ("Charging", (
        ("effective_charge_rate_kw", "Effective charge rate", " kW"),
        ("used_charge_rate_kw", "Charge rate used", " kW"),
        ("charge_rate_from_cache", "Rate reused from history", ""),
        ("battery_temp_c", "Battery temperature", " °C"),
        ("temp_deration_factor", "Temperature deration", " ×"),
        ("hourly_forecast_used", "Hourly forecast used", ""),
        ("synthetic_ramp", "Synthetic ramp used", ""),
        ("bridge_hour", "Solar covers load from", ":00"),
    )),
)


def _safe_id(value: object) -> str:
    """Reduce a config value to a safe identifier charset (alphanumeric/_/-).

    Used for the plant ID and inverter serial before they are interpolated into
    Jinja-rendered dashboard markdown and entity IDs, so template/quote
    characters can't inject.
    """
    return "".join(c for c in str(value).strip() if c.isalnum() or c in ("_", "-"))


def _safe_entity(value: object, default: str) -> str:
    """An entity id reduced to [a-z0-9_.] (it is interpolated into templates); the default if blank."""
    cleaned = "".join(c for c in str(value or "").strip().lower() if c.isalnum() or c in ("_", "."))
    return cleaned if "." in cleaned else default


# --------------------------------------------------------------------------- #
# Card helpers                                                                 #
# --------------------------------------------------------------------------- #


def _heading(text: str, icon: str, badges: list[dict] | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {"type": "heading", "heading": text, "heading_style": "title", "icon": icon}
    if badges:
        card["badges"] = badges
    return card


def _badge(entity: str, color: str | None = None, when_on: bool = False) -> dict[str, Any]:
    badge: dict[str, Any] = {"type": "entity", "entity": entity, "show_state": True, "show_icon": True}
    if color:
        badge["color"] = color
    if when_on:
        badge["visibility"] = [{"condition": "state", "entity": entity, "state": "on"}]
    return badge


def _view_badge(entity: str, name: str, color: str, visibility: list[dict] | None = None) -> dict[str, Any]:
    badge: dict[str, Any] = {"type": "entity", "entity": entity, "name": name, "show_name": True, "color": color}
    if visibility:
        badge["visibility"] = visibility
    return badge


def _tile(
    entity: str,
    name: str,
    icon: str,
    color: str,
    columns: int = 6,
    state_content: list[str] | None = None,
    action: dict | None = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "tile", "entity": entity, "name": name, "icon": icon, "color": color,
        "grid_options": {"columns": columns},
    }
    if state_content:
        card["state_content"] = state_content
    if action:
        card["tap_action"] = action
        card["icon_tap_action"] = action
    return card


def _md(content: str, macros: bool = True) -> dict[str, Any]:
    return {"type": "markdown", "text_only": True, "content": (_MACROS if macros else "") + content}


def _press(button: str, confirm: str | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"action": "perform-action", "perform_action": "button.press", "target": {"entity_id": button}}
    if confirm:
        action["confirmation"] = {"text": confirm}
    return action


def _service(service: str, confirm: str, data: dict | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"action": "perform-action", "perform_action": service, "confirmation": {"text": confirm}}
    if data:
        action["data"] = data
    return action


def _on(entity: str) -> list[dict[str, Any]]:
    return [{"condition": "state", "entity": entity, "state": "on"}]


def _section(*cards: dict[str, Any], span: int | None = None) -> dict[str, Any]:
    section: dict[str, Any] = {"type": "grid", "cards": list(cards)}
    if span:
        section["column_span"] = span
    return section


def _view(title: str, path: str, icon: str, sections: list[dict], badges: list[dict] | None = None) -> dict[str, Any]:
    view: dict[str, Any] = {
        "title": title, "path": path, "icon": icon, "type": "sections", "max_columns": 4, "sections": sections,
    }
    if badges:
        view["badges"] = badges
    return view


def _apex(title: str, series: list[dict], span: dict, graph_span: str = "24h", yaxis: list[dict] | None = None) -> dict[str, Any]:
    card: dict[str, Any] = {
        "type": "custom:apexcharts-card",
        "header": {"show": True, "title": title, "show_states": False},
        "graph_span": graph_span,
        "span": span,
        "now": {"show": True, "label": "Now"},
        "apex_config": {"chart": {"height": 230}, "legend": {"show": len(series) > 1}, "grid": {"strokeDashArray": 3}},
        "series": series,
        "grid_options": {"columns": 12},
    }
    if yaxis:
        card["yaxis"] = yaxis
    return card


# apexcharts data_generator bodies: turn attribute arrays into [timestamp, value] points.
def _mpc_points(field: str) -> str:
    return (
        "const a = entity.attributes; if (!a.slots || !a.date) return [];"
        " const base = new Date(a.date + 'T00:00:00');"
        " return a.slots.map((s) => { const [h, m] = s.time.split(':').map(Number);"
        " const t = new Date(base); t.setDate(t.getDate() + (s.day || 0)); t.setHours(h, m, 0, 0);"
        f" return [t.getTime(), s.{field}]; }});"
    )


_LOAD_PROFILE_POINTS = (
    "const a = entity.attributes; if (!a.slots) return [];"
    " const base = a.date ? new Date(a.date + 'T00:00:00') : new Date(new Date().setHours(0, 0, 0, 0));"
    " return a.slots.map((v, i) => [base.getTime() + i * 1800000, v]);"
)

_SPARE_SOLAR_POINTS = (
    "const hours = entity.attributes.hours || [];"
    " return hours.map((h) => [new Date().setHours(h.hour, 0, 0, 0), h.spare_kwh]);"
)


# --------------------------------------------------------------------------- #
# Views                                                                        #
# --------------------------------------------------------------------------- #


def _power_flow_card(s) -> dict[str, Any]:
    return {
        "type": "custom:sunsynk-power-flow-card",
        "cardstyle": "full",
        "wide": False,
        "large_font": False,
        "show_solar": True,
        "show_battery": True,
        "show_grid": True,
        "center_no_grid": True,
        "card_height": "100%",
        "card_width": "100%",
        "decimal_places": 2,
        "decimal_places_energy": 1,
        "dynamic_line_width": True,
        "max_line_width": 4,
        "min_line_width": 1,
        "inverter": {"modern": True, "autarky": "no", "model": "sunsynk", "colour": "#000000", "auto_scale": False},
        "battery": {
            "shutdown_soc": 20, "show_daily": True, "count": 1, "dynamic_colour": True, "linear_gradient": True,
            "show_remaining_energy": False, "remaining_energy_to_shutdown": True, "charge_colour": "#ffd505",
            "animate": True, "energy": 10, "auto_scale": False,
        },
        "battery2": {
            "energy": 0, "shutdown_soc": 20, "soc_end_of_charge": 100, "invert_power": False, "hide_soc": False,
            "colour": "#ffc0cb", "show_remaining_energy": True, "remaining_energy_to_shutdown": False,
            "show_absolute": False, "auto_scale": True, "dynamic_colour": True, "linear_gradient": True,
            "animate": True, "path_threshold": 100, "navigate": "", "invert_flow": False,
        },
        "solar": {"show_daily": True, "mppts": 2, "dynamic_colour": True, "pv1_name": "Ext", "pv2_name": "Main", "auto_scale": False},
        "load": {
            "show_daily": True, "dynamic_colour": True, "dynamic_icon": True, "invert_load": False, "show_aux": True,
            "aux_type": "mdi:car-battery", "aux_name": "UPS", "max_colour": "#000000", "off_colour": "#000000",
            "essential_name": "Home", "aux_dynamic_colour": True, "show_daily_aux": False,
        },
        "grid": {
            "show_daily_buy": True, "show_daily_sell": True, "show_nonessential": False, "grid_name": "Octopus Energy",
            "export_colour": "#54d11f", "grid_off_colour": "#000000", "no_grid_colour": "#000000",
        },
        "entities": {
            "inverter_voltage_154": s("inverter_voltager_phase_0"),
            "load_frequency_192": s("load_frequency"),
            "inverter_current_164": s("inverter_current_phase_0"),
            "inverter_power_175": s("load_total_power"),
            "grid_connected_status_194": s("grid_status"),
            "inverter_status_59": s("runstatus"),
            "day_battery_charge_70": s("battery_etoday_charge"),
            "day_battery_discharge_71": s("battery_etoday_discharge"),
            "battery_voltage_183": s("battery_voltage"),
            "battery_soc_184": s("battery_soc"),
            "battery_power_190": s("battery_power"),
            "battery_current_191": s("battery_current"),
            "day_grid_import_76": s("grid_etoday_from"),
            "day_grid_export_77": s("grid_etoday_to"),
            "day_load_energy_84": s("load_daily_used"),
            "essential_power": "sensor.sunsynk_essential_power",
            "day_pv_energy_108": s("pv_etoday"),
            "pv1_power_186": s("pv_mppt0_power"),
            "pv2_power_187": s("pv_mppt1_power"),
            "pv1_voltage_109": s("pv_mppt0_voltage"),
            "pv1_current_110": s("pv_mppt0_current"),
            "pv2_voltage_111": s("pv_mppt1_voltage"),
            "pv2_current_112": s("pv_mppt1_current"),
            "grid_ct_power_172": s("grid_pac"),
            "aux_power_166": s("load_ups_total_power"),
            "battery_temp_182": s("battery_temperature"),
            "battery_rated_capacity": s("battery_capacity"),
            "battery_status": s("battery_status"),
            "grid_power_169": s("grid_pac"),
        },
    }


_ALERTS = (
    ("binary_sensor.grid_outage", "error", "Grid outage",
     "Running on the battery since {{ state_attr('binary_sensor.grid_outage', 'start') or 'just now' }}"
     " — lowest SOC {{ a('binary_sensor.grid_outage', 'soc_low', '%') }}. Planning pauses until the grid is back."),
    ("binary_sensor.inverter_fault", "error", "Inverter fault",
     "{% set ev = state_attr('binary_sensor.inverter_fault', 'events') or [] %}"
     "{{ ev | map(attribute='text', default='event') | join(', ') if ev else 'The cloud reports a fault or protect state.' }}"),
    ("binary_sensor.monitor_only_mode", "info", "Monitor-only mode",
     "The optimizer plans and reports but makes no inverter writes."),
    ("switch.away_mode", "info", "Away mode",
     "Planning uses the away-from-home load calibration."),
)


def _overview(s, forecast_sensor: str) -> dict[str, Any]:
    alerts = [
        {**_md(f'<ha-alert alert-type="{kind}" title="{title}">{body}</ha-alert>'), "visibility": _on(entity)}
        for entity, kind, title, body in _ALERTS
    ]
    alerts.append({
        **_md(
            '<ha-alert alert-type="warning" title="Last error">{{ states(\'sensor.last_error\') }}</ha-alert>',
            macros=False,
        ),
        "visibility": [
            {"condition": "state", "entity": "sensor.last_error", "state_not": "OK"},
            {"condition": "state", "entity": "sensor.last_error", "state_not": "unavailable"},
        ],
    })
    alerts.append({
        **_md(
            '<ha-alert alert-type="success" title="Free electricity event: {{ states(\'sensor.free_event\') }}">'
            "{{ a('sensor.free_event', 'free_start') }} → {{ a('sensor.free_event', 'free_end') }}"
            " · sell {{ a('sensor.free_event', 'sell_status') }}</ha-alert>"
        ),
        "visibility": [{"condition": "state", "entity": "sensor.free_event", "state_not": "none"}],
    })

    live = _section(
        _heading("Live", "mdi:home-lightning-bolt-outline", [
            _badge(s("battery_soc"), "green"),
            _badge(s("grid_pac"), "red"),
            _badge("binary_sensor.evening_export_disabled", "orange", when_on=True),
        ]),
        *alerts,
        _power_flow_card(s),
    )

    tonight = _section(
        _heading("Tonight's plan", "mdi:weather-night", [_badge("sensor.operation_mode", "indigo")]),
        {"type": "gauge", "entity": s("battery_soc"), "name": "Battery SOC now", "min": 0, "max": 100, "needle": True,
         # Red below the 20% shutdown reserve, amber mid, green healthy.
         "severity": {"red": 0, "yellow": 20, "green": 50}, "grid_options": {"columns": 6}},
        {"type": "gauge", "entity": "sensor.current_soc_target", "name": "Tonight's target", "min": 0, "max": 100,
         "needle": True, "severity": {"red": 0, "yellow": 20, "green": 50}, "grid_options": {"columns": 6}},
        _tile("sensor.next_import_window", "Import window", "mdi:clock-start", "blue"),
        _tile("sensor.selected_full_charge_day", "Full-charge day", "mdi:calendar-star", "amber"),
        _tile("sensor.flux_2_action", "Evening export", "mdi:transmission-tower-export", "teal", columns=12),
        _md(
            "{% set e = state_attr('" + _PLAN + "', 'explanation') %}"
            "{% if e %}<ha-icon icon=\"mdi:lightbulb-on-outline\"></ha-icon> **Why:** {{ e }}"
            "{% else %}No plan yet. It runs at 01:55, or press **Test plan** for a dry run.{% endif %}",
            macros=False,
        ),
    )

    today = _section(
        _heading("Today", "mdi:white-balance-sunny"),
        _tile(forecast_sensor, "Solar forecast", "mdi:solar-power-variant", "amber"),
        _tile("sensor.spare_solar_today", "Spare solar", "mdi:washing-machine", "light-green"),
        _tile("sensor.cost_of_1_kwh_now", "Cost of 1 kWh now", "mdi:cash-clock", "deep-orange",
              state_content=["state", "source"]),
        _tile("sensor.battery_runtime", "Battery runtime left", "mdi:timer-sand", "green"),
        _tile("sensor.import_causes_today", "Grid import by cause", "mdi:transmission-tower-import", "red"),
        _tile("sensor.kpi_self_sufficiency", "Self-sufficiency", "mdi:home-battery-outline", "teal"),
        _md(
            "<ha-icon icon=\"mdi:clock-check-outline\"></ha-icon> **Best time for big loads**\n\n"
            "{% set st = state_attr('sensor.spare_solar_today', 'best_window_start') %}"
            "{% if st %}Spare solar **{{ st }}–{{ a('sensor.spare_solar_today', 'best_window_end') }}**"
            " ({{ a('sensor.spare_solar_today', 'best_window_kwh', ' kWh') }}). {% endif %}"
            "{% set ch = state_attr('sensor.cost_of_1_kwh_now', 'cheapest_hour_today') %}"
            "{% if ch is not none %}Cheapest hour left: **{{ ch }}:00** at"
            " {{ a('sensor.cost_of_1_kwh_now', 'cheapest_hour_pence', 'p') }}"
            " ({{ a('sensor.cost_of_1_kwh_now', 'cheapest_hour_source') }}).{% endif %}"
            "{% if not st and ch is none %}No spare solar or cheaper hour left today.{% endif %}"
        ),
    )

    actions = _section(
        _heading("Quick actions", "mdi:gesture-tap-button"),
        _tile("switch.away_mode", "Away mode", "mdi:airplane", "purple",
              action={"action": "toggle"}),
        _tile("button.test_plan", "Test plan (dry run)", "mdi:flask-outline", "blue",
              action=_press("button.test_plan")),
        _tile("sensor.current_soc_target", "Full charge tonight", "mdi:battery-charging-100", "green",
              action=_service("sunsynk_optimizer.set_tonight_target",
                              "Charge to 100% tonight? Applies at the 01:55 plan, one night only.", {"soc": 100})),
        _tile("sensor.next_import_window", "Skip tonight's charge", "mdi:battery-off-outline", "orange",
              action=_service("sunsynk_optimizer.skip_tonight", "Skip tonight's grid charge? One night only.")),
    )

    return _view(
        "Overview", "sunsynk-optimizer", "mdi:battery-heart-variant",
        [live, tonight, today, actions],
        badges=[
            _view_badge(s("battery_soc"), "Battery", "green"),
            _view_badge("sensor.current_soc_target", "Target", "blue"),
            _view_badge(s("pv_etoday"), "Solar today", "amber"),
            _view_badge("sensor.cost_of_1_kwh_now", "1 kWh now", "deep-orange"),
            _view_badge("sensor.operation_mode", "Mode", "indigo"),
            _view_badge("binary_sensor.grid_outage", "Grid outage", "red", _on("binary_sensor.grid_outage")),
            _view_badge("binary_sensor.inverter_fault", "Inverter fault", "red", _on("binary_sensor.inverter_fault")),
        ],
    )


def _plan_table() -> str:
    lines = []
    for group, rows in _PLAN_ROWS:
        lines += ["", f"| {group} | |", "|:--|--:|"]
        lines += [f"| {label} | {{{{ a('{_PLAN}', '{key}', '{unit}') }}}} |" for key, label, unit in rows]
    return "\n".join(lines)


def _plan(s, forecast_sensor: str, avg_consumption_kw: float) -> dict[str, Any]:
    why = _section(
        _heading("Why this plan?", "mdi:head-lightbulb-outline", [_badge(_PLAN, "blue")]),
        _md(
            "{% set e = state_attr('" + _PLAN + "', 'explanation') %}{% if e %}> {{ e }}\n{% endif %}"
            + _plan_table()
        ),
    )

    solar = _section(
        _heading("Solar forecast", "mdi:solar-power-variant"),
        _tile(forecast_sensor, "Forecast today", "mdi:weather-sunny", "amber"),
        _tile("sensor.solar_forecast_p50", "Learned P50", "mdi:chart-bell-curve", "orange"),
        _md(
            "| Likely range | kWh |\n|:--|--:|\n"
            "| Pessimistic (P10) | {{ a('sensor.solar_forecast_p50', 'p10_kwh') }} |\n"
            "| Median (P50) | {{ a('sensor.solar_forecast_p50', 'p50_kwh') }} |\n"
            "| Optimistic (P90) | {{ a('sensor.solar_forecast_p50', 'p90_kwh') }} |\n"
            "| Raw forecast | {{ a('sensor.solar_forecast_p50', 'raw_forecast_kwh') }} |\n\n"
            "<small>Learned from {{ a('sensor.solar_forecast_p50', 'days') }} days"
            " ({{ a('sensor.solar_forecast_p50', 'bin') }}).</small>"
        ),
        _apex(
            "Spare solar by hour",
            [{"entity": "sensor.spare_solar_today", "name": "Spare", "type": "column", "color": _SOLAR,
              "unit": "kWh", "float_precision": 2, "data_generator": _SPARE_SOLAR_POINTS}],
            {"start": "day"},
        ),
    )

    load = _section(
        _heading("House load forecast", "mdi:home-lightning-bolt"),
        _tile("sensor.load_profile", "Learned load today", "mdi:home-analytics", "blue"),
        _tile("sensor.effective_charge_rate", "Effective charge rate", "mdi:battery-charging-high", "green"),
        _apex(
            "Learned vs actual load (kWh per half hour)",
            [
                {"entity": "sensor.load_profile", "name": "Learned", "type": "area", "curve": "stepline",
                 "color": _LOAD, "opacity": 0.25, "unit": "kWh", "float_precision": 2,
                 "data_generator": _LOAD_PROFILE_POINTS},
                {"entity": s("load_total_power"), "name": "Actual", "type": "line", "curve": "stepline",
                 "color": _IMPORT, "unit": "kWh", "float_precision": 2,
                 "group_by": {"func": "avg", "duration": "30min"}, "transform": "return x / 2000;"},
            ],
            {"start": "day"},
        ),
        _md(
            "| Band | |\n|:--|--:|\n"
            "| Peak 16:00–19:00 | {{ a('sensor.load_profile', 'peak_kwh', ' kWh') }} |\n"
            "| Evening average | {{ a('sensor.load_profile', 'evening_kw', ' kW') }} |\n"
            "| Daytime average | {{ a('sensor.load_profile', 'day_kw', ' kW') }} |\n"
            "| Day type | {{ a('sensor.load_profile', 'day_type') }} |\n"
            "| Temperature scale | {{ a('sensor.load_profile', 'temp_scale', ' ×') }} |\n"
            "| Days learned | {{ a('sensor.load_profile', 'days') }} |"
        ),
    )

    learning = _section(
        _heading("Adaptive learning", "mdi:school-outline"),
        _tile("sensor.forecast_correction", "Forecast correction", "mdi:tune-vertical", "amber",
              state_content=["state", "days_collected"]),
        _tile("sensor.overnight_drain_adjustment", "Drain compensation", "mdi:battery-minus-outline", "orange",
              state_content=["state", "days_collected"]),
        _tile("sensor.evening_soc_adjustment", "Evening SOC nudge", "mdi:battery-sync-outline", "purple",
              state_content=["state", "days_collected"]),
        _tile("sensor.battery_capacity_estimate", "Measured capacity", "mdi:battery-high", "green"),
        _md(_learned_content(avg_consumption_kw), macros=False),
        _md(_seasonal_guidance(forecast_sensor), macros=False),
    )

    return _view("Plan", "plan", "mdi:chart-timeline-variant", [why, solar, load, learning])


def _learned_content(avg_consumption_kw: float) -> str:
    p = _PLAN
    return f"""{{% set solar_start = state_attr('{p}', 'solar_start_time') %}}
{{% set hrs = state_attr('{p}', 'hours_to_solar') | float(0) %}}
{{% set hourly_used = state_attr('{p}', 'hourly_forecast_used') %}}
{{% set bridge_h = state_attr('{p}', 'bridge_hour') %}}
{{% set cons = state_attr('{p}', 'avg_consumption_kw') | float({avg_consumption_kw}) %}}
{{% set factor = state_attr('{p}', 'forecast_correction_factor') | float(1.0) %}}
{{% set factor_days = state_attr('{p}', 'forecast_correction_days') | int(0) %}}
{{% set drain = state_attr('{p}', 'overnight_drain_adjustment') | int(0) %}}
{{% set drain_days = state_attr('{p}', 'overnight_drain_days') | int(0) %}}
{{% set nudge = state_attr('{p}', 'soc_adjustment') | int(0) %}}
{{% set nudge_days = state_attr('{p}', 'soc_adjustment_days') | int(0) %}}
{{% set derate = state_attr('{p}', 'temp_deration_factor') | float(1.0) %}}
<ha-icon icon="mdi:brain"></ha-icon> **What the system has learned**

{{% if hourly_used %}}- **Solar bridge:** solar covers the house from ~{{{{ bridge_h }}}}:00; the deficit before that is charged overnight.
{{% elif solar_start %}}- **Solar bridge:** ~{{{{ solar_start }}}} start, {{{{ hrs }}}} h × {{{{ cons }}}} kW = {{{{ (hrs * cons) | round(1) }}}} kWh to cover.
{{% endif %}}- **Forecast:** {{% if factor_days < 7 %}}collecting data ({{{{ factor_days }}}}/7 days).{{% elif factor < 0.95 %}}the forecast runs {{{{ '%.0f' | format((1 - factor) * 100) }}}}% high here.{{% elif factor > 1.05 %}}actual solar runs {{{{ '%.0f' | format((factor - 1) * 100) }}}}% above the forecast.{{% else %}}accurate to within 5%.{{% endif %}}
- **Overnight drain:** {{% if drain_days < 5 %}}collecting data ({{{{ drain_days }}}}/5 mornings).{{% elif drain > 0 %}}the battery loses ~{{{{ drain }}}}% before 06:00, added to the target.{{% else %}}negligible.{{% endif %}}
- **Evening nudge:** {{% if nudge_days < 5 %}}collecting data ({{{{ nudge_days }}}}/5 days).{{% elif nudge < 0 %}}the battery ends the day too full, so the target drops {{{{ nudge | abs }}}}%.{{% elif nudge > 0 %}}the battery ends the day too empty, so the target rises {{{{ nudge }}}}%.{{% else %}}in range, no nudge.{{% endif %}}
{{% if derate < 1.0 %}}- **Battery temperature:** charge rate derated to {{{{ '%.0f' | format(derate * 100) }}}}%.
{{% endif %}}"""


def _seasonal_guidance(forecast_sensor: str) -> str:
    return f"""{{% set forecast = states('{forecast_sensor}') | float(0) %}}
<ha-icon icon="mdi:weather-partly-cloudy"></ha-icon> **Seasonal guidance**

{{% if forecast >= 10 %}}**Summer-like.** Bias toward lower overnight import and shorter windows; trust solar recovery.
{{% elif forecast <= 5 %}}**Winter-like.** Bias toward higher overnight targets and longer windows; rely less on solar.
{{% else %}}**Shoulder day.** Mid targets; let the forecast shorten or extend the import window.
{{% endif %}}"""


def _energy(s) -> dict[str, Any]:
    c = "sensor.consumption"
    today = _section(
        _heading("Today's energy", "mdi:lightning-bolt-outline"),
        _tile(s("pv_etoday"), "Solar", "mdi:solar-power", "amber"),
        _tile(s("load_daily_used"), "House", "mdi:home-lightning-bolt-outline", "blue"),
        _tile(s("grid_etoday_from"), "Imported", "mdi:transmission-tower-import", "red"),
        _tile(s("grid_etoday_to"), "Exported", "mdi:transmission-tower-export", "teal"),
        _tile(s("battery_etoday_charge"), "Battery in", "mdi:battery-arrow-up-outline", "green"),
        _tile(s("battery_etoday_discharge"), "Battery out", "mdi:battery-arrow-down-outline", "light-green"),
        _md(
            "| Window | Load | Import | Export |\n|:--|--:|--:|--:|\n"
            f"| Overnight 00–06 | {{{{ a('{c}', 'overnight_load_kwh') }}}} | | |\n"
            f"| Peak 16–19 | {{{{ a('{c}', 'peak_load_kwh') }}}} | {{{{ a('{c}', 'peak_grid_import_kwh') }}}}"
            f" | {{{{ a('{c}', 'peak_grid_export_kwh') }}}} |\n"
            f"| Full day | {{{{ a('{c}', 'day_load_kwh') }}}} | {{{{ a('{c}', 'day_grid_import_kwh') }}}}"
            f" | {{{{ a('{c}', 'day_grid_export_kwh') }}}} |\n\n<small>kWh. Captured at 06:00, 19:00 and 22:00.</small>"
        ),
    )

    causes = "sensor.import_causes_today"
    cost = _section(
        _heading("Cost", "mdi:cash-multiple"),
        _md(
            f"<ha-icon icon=\"mdi:calendar-check\"></ha-icon> **Last settled day** ({{{{ a('{c}', 'daily_cost_date') }}}})\n\n"
            "| | |\n|:--|--:|\n"
            f"| Electricity import | {{{{ gbp('{c}', 'actual_import_cost_gbp') }}}} |\n"
            f"| Export income | {{{{ gbp('{c}', 'actual_export_income_gbp') }}}} |\n"
            f"| Gas | {{{{ gbp('{c}', 'actual_gas_cost_gbp') }}}} |\n"
            f"| **Net** | **{{{{ gbp('{c}', 'net_cost_gbp') }}}}** |\n\n"
            f"<ha-icon icon=\"mdi:calendar-range\"></ha-icon> **Year to date:** {{{{ gbp('{c}', 'year_to_date_net_cost_gbp') }}}}"
            f" over {{{{ a('{c}', 'year_to_date_days_counted') }}}} days"
        ),
        _md(
            "<ha-icon icon=\"mdi:transmission-tower-import\"></ha-icon> **Grid import today by cause**\n\n"
            "| Cause | kWh |\n|:--|--:|\n"
            + "\n".join(
                f"| {label} | {{{{ a('{causes}', '{key}') }}}} |"
                for key, label in (
                    ("grid_charge", "Planned charge"), ("load_over_limit", "Load over inverter limit"),
                    ("battery_floor", "Battery at floor"), ("slot_hold", "Peak-cover hold"),
                    ("free_event", "Free event"), ("unknown", "Other"),
                )
            )
        ),
        {"type": "entities", "title": "Bill check", "show_header_toggle": False,
         "entities": [{"entity": "number.billed_last_month", "name": "Billed last month (£)"}]},
        _md(
            "| Month | Logged | Gap | Days |\n|:--|--:|--:|--:|\n"
            "| {{ a('number.billed_last_month', 'month') }} | {{ gbp('number.billed_last_month', 'logged_gbp') }}"
            " | {{ gbp('number.billed_last_month', 'gap_gbp') }} | {{ a('number.billed_last_month', 'days_logged') }} |"
        ),
    )

    trends = _section(
        _heading("Daily KPIs (30 days)", "mdi:chart-bar"),
        _tile("sensor.kpi_avoidable_import", "Avoidable import", "mdi:cash-remove", "red"),
        _tile("sensor.kpi_export_peak", "Peak export income", "mdi:cash-plus", "teal"),
        _tile("sensor.kpi_unused_charge", "Unused charge", "mdi:battery-alert-variant-outline", "orange"),
        _tile("sensor.kpi_over_limit_import", "Over-limit import", "mdi:flash-alert-outline", "deep-orange"),
        {"type": "statistics-graph", "title": "£ per day", "chart_type": "line", "period": "hour",
         "days_to_show": 30, "stat_types": ["state"], "hide_legend": False,
         "entities": [{"entity": "sensor.kpi_avoidable_import", "name": "Avoidable import"},
                      {"entity": "sensor.kpi_export_peak", "name": "Peak export income"}]},
        {"type": "statistics-graph", "title": "Self-sufficiency %", "chart_type": "line", "period": "hour",
         "days_to_show": 30, "stat_types": ["mean"],
         "entities": [{"entity": "sensor.kpi_self_sufficiency", "name": "Self-sufficiency"}]},
    )

    history = _section(
        _heading("Last 48 hours", "mdi:history"),
        {"type": "history-graph", "title": "SOC vs target", "hours_to_show": 48,
         "entities": [{"entity": s("battery_soc"), "name": "Battery SOC"},
                      {"entity": "sensor.current_soc_target", "name": "Target SOC"}]},
        {"type": "history-graph", "title": "Grid power", "hours_to_show": 48,
         "entities": [{"entity": s("grid_pac"), "name": "Grid power"}]},
        {"type": "history-graph", "title": "PV generation", "hours_to_show": 48,
         "entities": [{"entity": s("pv_mppt0_power"), "name": "PV MPPT0"},
                      {"entity": s("pv_mppt1_power"), "name": "PV MPPT1"}]},
    )

    return _view("Energy & cost", "energy", "mdi:cash-multiple", [today, cost, trends, history])


def _system(s) -> dict[str, Any]:
    h, cap = "sensor.battery_soh", "sensor.battery_capacity_estimate"
    battery = _section(
        _heading("Battery health", "mdi:battery-heart-outline"),
        _tile(h, "State of health", "mdi:battery-heart-variant", "green", state_content=["state", "cycles_max"]),
        _tile(cap, "Measured capacity", "mdi:battery-high", "teal", state_content=["state", "pct_of_configured"]),
        _tile("sensor.battery_runtime", "Runtime left", "mdi:timer-sand", "light-green"),
        _tile(s("battery_temperature"), "Battery temperature", "mdi:thermometer", "orange"),
        _md(
            "| BMS | |\n|:--|--:|\n"
            f"| Packs | {{{{ a('{h}', 'pack_count') }}}} |\n"
            f"| Cycles (max pack) | {{{{ a('{h}', 'cycles_max') }}}} |\n"
            f"| Nameplate / healthy | {{{{ a('{h}', 'nameplate_kwh', ' kWh') }}}} / {{{{ a('{h}', 'health_kwh', ' kWh') }}}} |\n"
            f"| Cell spread | {{{{ a('{h}', 'cell_spread_mv', ' mV') }}}} |\n"
            f"| Cell / inverter temp | {{{{ a('{h}', 'cell_temp_c', ' °C') }}}} / {{{{ a('{h}', 'inverter_temp_c', ' °C') }}}} |\n"
            f"| BMS charge / discharge limit | {{{{ a('{h}', 'bms_charge_limit_a', ' A') }}}} / {{{{ a('{h}', 'bms_discharge_limit_a', ' A') }}}} |\n"
            f"| Capacity samples | {{{{ a('{cap}', 'samples') }}}} |\n"
            f"{{% set fw = state_attr('{h}', 'fault_words') %}}{{% if fw %}}\n"
            "<ha-alert alert-type=\"warning\" title=\"BMS fault words\">"
            "{% for k, v in fw.items() %}{{ k }} ({{ v }}){{ ', ' if not loop.last }}{% endfor %}</ha-alert>{% endif %}"
        ),
    )

    health = _section(
        _heading("System health", "mdi:heart-pulse"),
        _tile("binary_sensor.grid_outage", "Grid", "mdi:transmission-tower", "red"),
        _tile("binary_sensor.inverter_fault", "Inverter", "mdi:alert-circle-outline", "red"),
        _tile("sensor.data_health", "Data gaps (14 days)", "mdi:database-check-outline", "amber"),
        _tile("sensor.pv_string_share", "PV string 1 share", "mdi:solar-panel", "amber",
              state_content=["state", "weaker"]),
        _tile("sensor.last_error", "Last error", "mdi:alert-outline", "deep-orange", columns=12),
        _tile("sensor.last_updated", "Last updated", "mdi:update", "grey", columns=12),
        _md(
            "{% set dh = states.sensor.data_health %}"
            "{% if dh and dh.attributes %}{% for kind, items in dh.attributes.items() if items is sequence and items is not string %}"
            "<ha-alert alert-type=\"warning\" title=\"{{ kind | capitalize }} records\">{{ items | join(', ') }}</ha-alert>"
            "{% endfor %}{% endif %}"
            "{% if states('sensor.data_health') == '0' %}✅ No data gaps in the last 14 days.{% endif %}"
            "{% set last = state_attr('binary_sensor.grid_outage', 'end') %}"
            "{% if last %}\n<small>Last outage ended {{ last }} (lowest SOC"
            " {{ a('binary_sensor.grid_outage', 'soc_low', '%') }}).</small>{% endif %}"
        ),
    )

    live = _section(
        _heading("Inverter live", "mdi:sine-wave"),
        _tile(s("grid_pac"), "Grid power", "mdi:transmission-tower", "red"),
        _tile(s("load_total_power"), "House load", "mdi:home-lightning-bolt-outline", "blue"),
        _tile(s("pv_mppt0_power"), "PV string 1", "mdi:solar-panel", "amber"),
        _tile(s("pv_mppt1_power"), "PV string 2", "mdi:solar-panel", "amber"),
        _tile(s("battery_power"), "Battery power", "mdi:battery-charging", "green"),
        _tile(s("battery_soc"), "Battery SOC", "mdi:battery", "green"),
        {"type": "logbook", "title": "Optimizer activity", "hours_to_show": 24,
         "entities": [
             "sensor.selected_full_charge_day", _PLAN, "sensor.flux_2_action", "sensor.last_error",
             "binary_sensor.evening_export_disabled", "binary_sensor.monitor_only_mode",
             "binary_sensor.grid_outage", "binary_sensor.inverter_fault", "sensor.free_event", "switch.away_mode",
         ]},
    )

    return _view("Battery & system", "system", "mdi:heart-pulse", [battery, health, live])


def _shadow(s) -> dict[str, Any]:
    m, d, t, f, u = (
        "sensor.mpc_plan", "sensor.dynamic_tariff_shadow", "sensor.tou_shadow", "sensor.flex_event_offer",
        "sensor.upgrade_advisor",
    )
    intro = _md(
        "<ha-alert alert-type=\"info\" title=\"Shadow lab\">Features here run alongside the live plan and only log"
        " what they would have done. Nothing on this page writes to the inverter.</ha-alert>",
        macros=False,
    )
    scorecard = _section(
        _heading("Scorecard", "mdi:clipboard-check-outline", [_badge("sensor.shadow_scorecard", "purple")]),
        intro,
        _md(
            "{% set icons = {'ready': '🟢', 'not_yet': '🟡', 'harmful': '🔴', 'no_records': '⚪'} %}"
            "{% set sc = states.sensor.shadow_scorecard %}"
            "{% set rows = namespace(n=0) %}"
            "| Feature | Verdict | Days | £ |\n|:--|:--|--:|--:|\n"
            "{% if sc %}{% for name, v in sc.attributes.items() if v is mapping %}{% set rows.n = rows.n + 1 %}"
            "| {{ name | replace('_', ' ') }} | {{ icons.get(v.verdict, '') }} {{ (v.verdict or '') | replace('_', ' ') }}"
            " | {{ v.days if v.days is not none else '—' }}"
            " | {{ '%.2f' | format(v.delta_gbp) if v.delta_gbp is number else '—' }} |\n{% endfor %}{% endif %}"
            "{% if rows.n == 0 %}| No scorecard yet (first Sunday of the month) | | | |{% endif %}",
            macros=False,
        ),
    )

    mpc = _section(
        _heading("MPC planner", "mdi:chart-timeline", [_badge(m, "purple")]),
        _apex(
            "Planned SOC and grid, next 24 h",
            [
                {"entity": m, "name": "SOC", "type": "line", "curve": "smooth", "color": _BATTERY, "unit": "%",
                 "yaxis_id": "soc", "data_generator": _mpc_points("soc")},
                {"entity": m, "name": "Grid", "type": "column", "color": _SHADOW, "unit": "kWh",
                 "float_precision": 2, "yaxis_id": "grid", "data_generator": _mpc_points("grid_kwh")},
            ],
            {"start": "hour"},
            yaxis=[
                {"id": "soc", "min": 0, "max": 100, "decimals": 0},
                {"id": "grid", "opposite": True, "decimals": 1},
            ],
        ),
        _md(
            "| Plan | MPC | Rules |\n|:--|--:|--:|\n"
            f"| SOC at 05:00 | {{{{ a('{m}', 'soc_0500', '%') }}}} | {{{{ a('{m}', 'rule_target_soc', '%') }}}} |\n"
            f"| P10 / P90 solar SOC | {{{{ a('{m}', 'soc_0500_p10', '%') }}}} / {{{{ a('{m}', 'soc_0500_p90', '%') }}}} | |\n"
            f"| Off-peak / daytime charge | {{{{ a('{m}', 'offpeak_charge_kwh', ' kWh') }}}} / {{{{ a('{m}', 'day_charge_kwh', ' kWh') }}}} | |\n"
            f"| Export | {{{{ a('{m}', 'export_kwh', ' kWh') }}}} | |\n"
            f"| 24 h cost | {{{{ gbp('{m}', 'cost_gbp') }}}} | |\n\n"
            f"<small>Planned at {{{{ a('{m}', 'time') }}}} from {{{{ a('{m}', 'soc', '%') }}}}.</small>"
        ),
    )

    agile = _section(
        _heading("Agile vs Flux", "mdi:swap-horizontal", [_badge(d, "purple")]),
        _md(
            f"{{% set skip = state_attr('{d}', 'skipped') %}}{{% if skip %}}"
            "<ha-alert alert-type=\"info\">Skipped tonight: {{ skip | replace('_', ' ') }}.</ha-alert>{% else %}"
            "| Next 24 h | |\n|:--|--:|\n"
            f"| Flux cost | {{{{ gbp('{d}', 'flux_cost_gbp') }}}} |\n"
            f"| Agile cost | {{{{ gbp('{d}', 'agile_cost_gbp') }}}} |\n"
            f"| **Agile saving** | **{{{{ gbp('{d}', 'saving_gbp') }}}}** |\n"
            f"| Agile price range | {{{{ a('{d}', 'min_p', 'p') }}}} – {{{{ a('{d}', 'max_p', 'p') }}}} |\n"
            f"| Spike / negative slots | {{{{ a('{d}', 'spike_slots') }}}} / {{{{ a('{d}', 'negative_slots') }}}} |\n"
            f"| Cheapest charge windows | {{{{ (state_attr('{d}', 'cheapest_windows') or []) | map('join', '–') | join(', ') or '—' }}}} |\n"
            "{% endif %}"
        ),
    )

    tou = _section(
        _heading("Inverter TOU check", "mdi:timetable", [_badge(t, "purple")]),
        _md(
            f"{{% set pr = state_attr('{t}', 'proposed') or [] %}}{{% set inv = state_attr('{t}', 'inverter') or [] %}}"
            f"{{% if not state_attr('{t}', 'readable') and pr %}}<ha-alert alert-type=\"warning\">"
            "Couldn't read the inverter's time-of-use slots.</ha-alert>\n{% endif %}"
            "{% if pr %}| Slot | Plan | Inverter | |\n|:--|:--|:--|:-:|\n"
            "{% for p in pr %}{% set i = inv[loop.index0] if inv | length > loop.index0 else none %}"
            "| {{ loop.index }} | {{ p.time }} · {{ p.soc }}%{{ ' ⚡' if p.grid_charge else '' }}"
            " | {% if i %}{{ i.time }} · {{ i.soc }}%{{ ' ⚡' if i.grid_charge else '' }}{% else %}—{% endif %}"
            " | {{ '✅' if i and i.time == p.time and i.soc == p.soc and i.grid_charge == p.grid_charge else '❌' }} |\n"
            "{% endfor %}\n<small>⚡ = grid charge. Read-only: the slots are never written.</small>"
            "{% else %}No plan compared yet; it runs after the 01:55 plan.{% endif %}",
            macros=False,
        ),
    )

    flex = _section(
        _heading("Flex event offer", "mdi:lightning-bolt-circle", [_badge(f, "purple")]),
        _md(
            f"{{% if state_attr('{f}', 'date') %}}| Last event | |\n|:--|--:|\n"
            f"| When | {{{{ a('{f}', 'date') }}}} {{{{ a('{f}', 'time') }}}} ({{{{ a('{f}', 'hours', ' h') }}}}) |\n"
            f"| Price | {{{{ a('{f}', 'price_p', 'p/kWh') }}}} |\n"
            f"| SOC / floor | {{{{ a('{f}', 'soc', '%') }}}} / {{{{ a('{f}', 'floor_soc', '%') }}}} |\n"
            f"| Could offer | {{{{ a('{f}', 'offer_kwh', ' kWh') }}}} |\n"
            f"| Value / gain | {{{{ gbp('{f}', 'value_gbp') }}}} / {{{{ gbp('{f}', 'gain_gbp') }}}} |"
            "{% else %}No flex event seen yet. Set a flex event entity in the options to track offers.{% endif %}"
        ),
    )

    upgrade = _section(
        _heading("Upgrade advisor", "mdi:home-plus-outline", [_badge(u, "purple")]),
        _md(
            f"{{% set opts = state_attr('{u}', 'options') or [] %}}{{% if opts %}}"
            "| Option | £/yr | Payback |\n|:--|--:|--:|\n"
            "{% for o in opts %}| {{ o.name }} | {{ '-' if o.annual_gbp < 0 else '' }}£{{ '%.0f' | format(o.annual_gbp | abs) }}"
            " | {{ o.payback_years ~ ' yrs' if o.payback_years else '—' }} |\n{% endfor %}"
            f"\n<small>From {{{{ a('{u}', 'days') }}}} logged days, run {{{{ a('{u}', 'date') }}}}."
            " Battery and PV figures are upper bounds; the heat pump is low confidence.</small>"
            "{% else %}The first report runs with the monthly reports.{% endif %}"
        ),
    )

    return _view("Shadow lab", "shadow", "mdi:flask-outline", [scorecard, mpc, agile, tou, flex, upgrade])


def _controls(api_plant_id: str, serial: str, weather_entity: str, forecast_sensor: str) -> dict[str, Any]:
    actions = _section(
        _heading("Actions", "mdi:gesture-tap-button"),
        _tile("switch.away_mode", "Away mode", "mdi:airplane", "purple", action={"action": "toggle"}),
        _tile("button.test_plan", "Test plan (dry run)", "mdi:flask-outline", "blue", action=_press("button.test_plan")),
        _tile("sensor.current_soc_target", "Full charge tonight", "mdi:battery-charging-100", "green",
              action=_service("sunsynk_optimizer.set_tonight_target",
                              "Charge to 100% tonight? Applies at the 01:55 plan, one night only.", {"soc": 100})),
        _tile("sensor.next_import_window", "Skip tonight's charge", "mdi:battery-off-outline", "orange",
              action=_service("sunsynk_optimizer.skip_tonight", "Skip tonight's grid charge? One night only.")),
        _tile("button.reset_to_baseline", "Reset baseline", "mdi:restore", "grey",
              action=_press("button.reset_to_baseline", "Reset the optimizer to its baseline settings?")),
        _tile("button.install_dashboard", "Update dashboard", "mdi:view-dashboard-edit", "indigo",
              action=_press("button.install_dashboard")),
        _md(
            "<small>Another one-night target: **Developer tools → Actions →"
            " Sunsynk Optimizer: Set tonight's target**.</small>",
            macros=False,
        ),
    )

    free = _section(
        _heading("Free electricity event", "mdi:gift-outline", [_badge("sensor.free_event", "green")]),
        {"type": "entities", "show_header_toggle": False,
         "entities": [{"entity": "datetime.free_event_start", "name": "Start"},
                      {"entity": "datetime.free_event_end", "name": "End"}]},
        _tile("button.cancel_free_event", "Cancel free event", "mdi:flash-off", "red",
              columns=12, action=_press("button.cancel_free_event", "Cancel the free electricity event?")),
        {"type": "entities", "title": "Status", "show_header_toggle": False,
         "entities": [
             {"entity": "sensor.free_event", "name": "Phase"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "free_start", "name": "Free start"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "free_end", "name": "Free end"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "sell_status", "name": "Sell"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "sell_floor_soc", "name": "Sell floor SOC (%)"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "expected_export_kwh", "name": "Expected export (kWh)"},
             {"entity": "sensor.free_event", "type": "attribute", "attribute": "expected_refill_kwh", "name": "Expected refill (kWh)"},
         ]},
    )

    about = _section(
        _heading("This install", "mdi:information-outline"),
        _tile("sensor.operation_mode", "Operation mode", "mdi:cog-outline", "indigo"),
        _tile("binary_sensor.monitor_only_mode", "Monitor only", "mdi:eye-outline", "grey"),
        _md(
            f"| | |\n|:--|--:|\n| Plant ID | {api_plant_id} |\n| Inverter S/N | {serial} |\n"
            f"| Weather | {weather_entity} |\n| Solar forecast | {forecast_sensor} |\n\n"
            "<small>Needs the HACS frontend cards **sunsynk-power-flow-card** and **apexcharts-card**."
            " Press **Update dashboard** after an upgrade that changes the layout.</small>",
            macros=False,
        ),
    )

    return _view("Controls", "controls", "mdi:tune-variant", [actions, free, about])


def _build_dashboard(config: dict) -> dict:
    """Build the full Lovelace dashboard as a Python dict.

    The dict is later serialised with json.dumps (not YAML) because the Lovelace
    YAML-mode loader accepts JSON. All SolarSynkV3 entity IDs are constructed via
    the local s() helper using inverter_serial so the dashboard is portable across
    installs with different serials.
    """
    # These config values are interpolated into markdown cards that Home Assistant
    # renders as Jinja templates, and into entity IDs. Strip to a safe charset so a
    # value containing template/quote characters ({ } ' ") can't inject.
    serial = _safe_id(config[CONF_INVERTER_SERIAL])
    api_plant_id = _safe_id(config[CONF_PLANT_ID])
    forecast_sensor = _safe_entity(config.get("solar_forecast_sensor"), "sensor.energy_production_today")
    weather_entity = _safe_entity(config.get("weather_entity"), "weather.forecast_home")
    avg_consumption_kw = float(config.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))

    def s(name: str) -> str:
        return f"sensor.solarsynkv3_{serial}_{name}"

    return {
        "title": "Sunsynk Optimizer",
        "views": [
            _overview(s, forecast_sensor),
            _plan(s, forecast_sensor, avg_consumption_kw),
            _energy(s),
            _system(s),
            _shadow(s),
            _controls(api_plant_id, serial, weather_entity, forecast_sensor),
        ],
    }


async def async_install_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Generate dashboard YAML from config-entry values and notify the user."""
    config = merge_entry_data(dict(entry.data), dict(entry.options))
    dashboard = _build_dashboard(config)

    config_dir = Path(hass.config.config_dir)
    filename = f"sunsynk_optimizer_{entry.entry_id}.yaml"
    dst = config_dir / filename
    content = json.dumps(dashboard, indent=2)
    try:
        await hass.async_add_executor_job(dst.write_text, content, "utf-8")
    except Exception as err:
        _LOGGER.error("Dashboard install failed: %s", err)
        persistent_notification.async_create(
            hass,
            f"Dashboard file could not be written to {dst}:\n{err}",
            title="Sunsynk Optimizer dashboard install failed",
            notification_id="sunsynk_optimizer_dashboard_install",
        )
        return

    snippet = f"""Add this to configuration.yaml, then reload dashboards via Developer Tools → YAML → Lovelace dashboards (or restart Home Assistant):

lovelace:
  dashboards:
    sunsynk-optimizer:
      mode: yaml
      filename: {filename}
      title: Sunsynk Optimizer
      icon: mdi:battery-heart-variant
      show_in_sidebar: true

The dashboard needs two HACS frontend cards: sunsynk-power-flow-card and apexcharts-card.

Dashboard file written to:
{dst}
"""

    persistent_notification.async_create(
        hass,
        snippet,
        title="Sunsynk Optimizer dashboard installed",
        notification_id="sunsynk_optimizer_dashboard_install",
    )
