# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Sensors for Sunsynk Optimizer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .data_logger import DataLogger
from .entity import optimizer_device_info, pin_entity_id
from .planning import KPI_SENSORS, clip_text, explain_plan, free_event_attributes, kpi_sensor_values, pv_string_attributes, upgrade_options


_ADAPTIVE_THRESHOLDS = {
    "forecast_correction": DataLogger._MIN_DAYS_FORECAST_CORRECTION,
    "overnight_drain_adjustment": DataLogger._MIN_DAYS_SOC_ADJUSTMENT,
    "evening_soc_adjustment": DataLogger._MIN_DAYS_SOC_ADJUSTMENT,
    "effective_charge_rate": 3,       # min for compute_effective_charge_rate_kw
}

# Adaptive sensor → (last_import_plan value key, default, progress-count key).
_ADAPTIVE_PLAN_KEYS = {
    "forecast_correction": ("forecast_correction_factor", 1.0, "forecast_correction_days"),
    "overnight_drain_adjustment": ("overnight_drain_adjustment", 0, "overnight_drain_days"),
    "evening_soc_adjustment": ("soc_adjustment", 0, "soc_adjustment_days"),
    "effective_charge_rate": ("effective_charge_rate_kw", None, "charge_rate_calibration_days"),
}

# Sensors that expose one OptimizerState field directly.
_DIRECT_FIELDS = {
    "selected_full_charge_day": "selected_full_charge_day",
    "operation_mode": "operation_mode",
    "current_soc_target": "current_soc_target",
    "next_import_window": "next_import_window",
    "last_updated": "updated_at",
}


# Always a number or unknown, so safe to record as long-term statistics.
_MEASUREMENT_KEYS = frozenset({
    "current_soc_target", "forecast_correction", "overnight_drain_adjustment", "evening_soc_adjustment",
    "effective_charge_rate", "battery_capacity_estimate", "battery_soh", "battery_runtime", "pv_strings",
    "marginal_cost", "solar_forecast_p50", "load_profile",
})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register all optimizer sensor entities for this config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SunsynkOptimizerSensor(coordinator, entry, "selected_full_charge_day", "Selected full charge day"),
            # Explicit entity id: the dashboard references the pre-rename id.
            SunsynkOptimizerSensor(coordinator, entry, "import_plan_end", "Import plan", entity_id="sensor.import_plan_end"),
            SunsynkOptimizerSensor(coordinator, entry, "flux2_action", "Flux 2 action"),
            SunsynkOptimizerSensor(coordinator, entry, "next_import_window", "Next import window"),
            SunsynkOptimizerSensor(coordinator, entry, "current_soc_target", "Current SOC target"),
            SunsynkOptimizerSensor(coordinator, entry, "operation_mode", "Operation mode"),
            SunsynkOptimizerSensor(coordinator, entry, "last_error", "Last error", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(coordinator, entry, "last_updated", "Last updated", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(coordinator, entry, "forecast_correction", "Forecast correction", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(coordinator, entry, "overnight_drain_adjustment", "Overnight drain adjustment", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(coordinator, entry, "evening_soc_adjustment", "Evening SOC adjustment", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(coordinator, entry, "effective_charge_rate", "Effective charge rate", entity_category=EntityCategory.DIAGNOSTIC),
            SunsynkOptimizerSensor(
                coordinator, entry, "battery_capacity_estimate", "Battery capacity estimate",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.battery_capacity_estimate",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "battery_soh", "Battery state of health",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.battery_soh",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "shadow_scorecard", "Shadow scorecard",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.shadow_scorecard",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "mpc_plan", "MPC plan (shadow)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.mpc_plan",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "dynamic_tariff", "Agile vs Flux saving (shadow)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.dynamic_tariff_shadow",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "tou_shadow", "Inverter TOU differences (shadow)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.tou_shadow",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "flex_event", "Flex event offer (shadow)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.flex_event_offer",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "upgrade_advisor", "Upgrade advisor best saving",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.upgrade_advisor",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "solar_forecast_p50", "Solar forecast P50 (learned)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.solar_forecast_p50",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "load_profile", "Load forecast today (learned)",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.load_profile",
            ),
            SunsynkOptimizerSensor(coordinator, entry, "consumption", "Consumption"),
            SunsynkOptimizerSensor(coordinator, entry, "free_event", "Free event"),
            SunsynkOptimizerSensor(coordinator, entry, "spare_solar", "Spare solar today"),
            SunsynkOptimizerSensor(
                coordinator, entry, "import_causes", "Import causes today", entity_id="sensor.import_causes_today"
            ),
            SunsynkOptimizerSensor(coordinator, entry, "health", "Health", entity_id="sensor.health"),
            SunsynkOptimizerSensor(
                coordinator, entry, "api_success_rate", "Cloud API success rate",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.api_success_rate",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "data_health", "Data health gaps",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.data_health",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "battery_runtime", "Battery runtime left", entity_id="sensor.battery_runtime"
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "pv_strings", "PV string 1 share",
                entity_category=EntityCategory.DIAGNOSTIC, entity_id="sensor.pv_string_share",
            ),
            SunsynkOptimizerSensor(
                coordinator, entry, "marginal_cost", "Cost of 1 kWh now", entity_id="sensor.cost_of_1_kwh_now"
            ),
        ]
        + [KpiSensor(coordinator, entry, *spec) for spec in KPI_SENSORS]
    )


class KpiSensor(CoordinatorEntity, SensorEntity):
    """One 22:00 KPI as a long-term-statistics sensor (phase 47). Unknown, never 0, when unavailable."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str, _field: str, name: str, unit: str, kind: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        pin_entity_id(self, "sensor", name, f"sensor.{key}")
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = optimizer_device_info(entry)
        if kind == "money":
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_suggested_display_precision = 2
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        return kpi_sensor_values(self.coordinator.state.last_day_kpis).get(self._key)

    @property
    def last_reset(self) -> datetime | None:
        if self._attr_state_class != SensorStateClass.TOTAL:
            return None
        day = dt_util.parse_date(str(_as_dict(self.coordinator.state.last_day_kpis).get("date") or ""))
        return dt_util.start_of_local_day(day) if day else None


class SunsynkOptimizerSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        sensor_key: str,
        name: str,
        entity_category: EntityCategory | None = None,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"
        pin_entity_id(self, "sensor", name, entity_id)
        if sensor_key in _MEASUREMENT_KEYS:
            self._attr_state_class = SensorStateClass.MEASUREMENT  # long-term statistics (phase 94)
        self._attr_entity_category = entity_category
        self._attr_device_info = optimizer_device_info(entry)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self._sensor_key == "marginal_cost":
            return "p/kWh"
        if self._sensor_key == "battery_runtime":
            return "h"
        if self._sensor_key in ("dynamic_tariff", "upgrade_advisor"):
            return "GBP"
        if self._sensor_key in ("api_success_rate", "pv_strings", "battery_soh", "current_soc_target", "overnight_drain_adjustment", "evening_soc_adjustment"):
            return "%"
        if self._sensor_key == "effective_charge_rate":
            return "kW"
        if self._sensor_key in ("battery_capacity_estimate", "solar_forecast_p50", "load_profile"):
            return "kWh"
        return "kWh" if self._sensor_key in ("spare_solar", "import_causes") else None

    @property
    def native_value(self) -> str | int | None:
        """Return the primary display value for this sensor.

        Each sensor_key maps to a specific field or derived string from OptimizerState.
        The import_plan_end and flux2_action sensors also expose rich extra_state_attributes.
        """
        state = self.coordinator.state

        if self._sensor_key in _DIRECT_FIELDS:
            return getattr(state, _DIRECT_FIELDS[self._sensor_key])
        if self._sensor_key == "last_error":
            return clip_text(state.last_error) or "OK"

        # Adaptive learning sensors — read from last_import_plan attributes.
        # Default values keep history continuous even before the first plan runs.
        if self._sensor_key in _ADAPTIVE_PLAN_KEYS:
            key, default, _ = _ADAPTIVE_PLAN_KEYS[self._sensor_key]
            return _as_dict(state.last_import_plan).get(key, default)

        if self._sensor_key == "consumption":
            load = _as_dict(state.last_day_actuals).get("day_load_kwh")
            return f"{load} kWh today" if load is not None else None

        if self._sensor_key == "free_event":
            return _as_dict(state.free_event).get("phase") or "none"


        if self._sensor_key == "spare_solar":
            return _as_dict(state.spare_solar).get("total_kwh")

        if self._sensor_key == "marginal_cost":
            return _as_dict(state.marginal_cost).get("pence")

        if self._sensor_key == "import_causes":
            return _as_dict(state.import_causes).get("total_kwh")

        if self._sensor_key == "health":
            return _as_dict(state.daily_health).get("status")

        if self._sensor_key == "api_success_rate":
            return _as_dict(state.api_health).get("success_rate_pct")

        if self._sensor_key == "data_health":
            return _as_dict(state.data_health).get("count")

        if self._sensor_key == "battery_runtime":
            return _as_dict(state.battery_runtime).get("hours")

        if self._sensor_key == "pv_strings":
            share = _as_dict(state.pv_strings).get("share")
            return None if share is None else round(share * 100, 1)

        if self._sensor_key == "battery_capacity_estimate":
            return _as_dict(state.battery_capacity_estimate).get("estimate_kwh")

        if self._sensor_key == "battery_soh":
            return _as_dict(_as_dict(state.battery_health).get("latest")).get("soh_pct")

        if self._sensor_key == "mpc_plan":
            return _as_dict(state.mpc_plan).get("first_action")

        if self._sensor_key == "dynamic_tariff":
            return _as_dict(state.dynamic_tariff).get("saving_gbp")

        if self._sensor_key == "tou_shadow":
            return _as_dict(state.tou_shadow).get("diff_count")

        if self._sensor_key == "flex_event":
            return _as_dict(state.flex_event).get("decision")

        if self._sensor_key == "upgrade_advisor":
            options = upgrade_options(_as_dict(state.upgrade_advisor))
            return options[0]["annual_gbp"] if options else None

        if self._sensor_key == "solar_forecast_p50":
            return _as_dict(_as_dict(state.last_import_plan).get("forecast_quantiles")).get("p50_kwh")

        if self._sensor_key == "load_profile":
            return _as_dict(state.load_profile).get("total_kwh")

        if self._sensor_key == "shadow_scorecard":
            card = _as_dict(state.shadow_scorecard)
            return sum(1 for v in card.values() if isinstance(v, dict) and v.get("verdict") == "ready") if card else None

        if self._sensor_key == "import_plan_end":
            plan = state.last_import_plan
            if not isinstance(plan, dict) or not plan:
                return None
            end = plan.get("flux1_end")
            target = plan.get("target_soc")
            if end and target is not None:
                return f"02:00→{end} target {target}%"
            return clip_text(str(plan))

        if self._sensor_key == "flux2_action":
            action = state.last_flux2_action
            if not action:
                return None
            if isinstance(action, str):
                return clip_text(action)
            if not isinstance(action, dict):
                return clip_text(str(action))

            name = action.get("action", "unknown")
            soc = action.get("soc")
            grid_pac = action.get("grid_pac")
            payload = action.get("payload")

            if name == "disable_evening_export":
                if grid_pac is not None:
                    return f"Export disabled, target 100% ({round(grid_pac)}W)"
                return "Export disabled, target 100%"
            # Action names keep "82" for history; the target can be higher when
            # floored at the evening reserve (phase 16), so read it from the payload.
            flux2 = payload.get("flux_2", {}) if isinstance(payload, dict) else {}
            target = flux2.get("targetSoc", 82)
            if name == "trim_to_82":
                end = flux2.get("endTime")
                if end:
                    return f"Trim to {target}% until {end}"
                if soc is not None:
                    return f"Trim to {target}% (SOC {soc}%)"
                return f"Trim to {target}%"
            if name == "peak_export":
                return f"Peak export to {target}% until {flux2.get('endTime', '19:00')}"
            if name == "full_day_trim_to_82":
                return f"Full charge trim to {target}%"
            if name == "schedule_full_trim":
                return "Full charge trim scheduled"
            if name == "none":
                return "No action"
            return str(name)

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.state
        if self._sensor_key == "import_plan_end" and isinstance(state.last_import_plan, dict):
            return {**state.last_import_plan, "explanation": " ".join(explain_plan(state.last_import_plan))}
        if self._sensor_key == "flux2_action":
            action = state.last_flux2_action
            if isinstance(action, dict):
                return action
            if isinstance(action, str):
                return {"raw": action}
        if self._sensor_key == "selected_full_charge_day":
            return {"scores": state.last_full_charge_scores}
        if self._sensor_key == "free_event":
            return free_event_attributes(_as_dict(state.free_event))
        if self._sensor_key == "spare_solar":
            spare = _as_dict(state.spare_solar)
            window = _as_dict(spare.get("window"))
            return {
                "best_window_start": window.get("start"),
                "best_window_end": window.get("end"),
                "best_window_kwh": window.get("kwh"),
                "hours": spare.get("hours", []),
            }
        if self._sensor_key == "battery_capacity_estimate":
            return _as_dict(state.battery_capacity_estimate)
        if self._sensor_key == "battery_soh":
            health = _as_dict(state.battery_health)
            latest = {k: v for k, v in _as_dict(health.get("latest")).items() if k != "soh_pct"}
            return {**latest, "trend": _as_dict(health.get("trend")), "checked": health.get("checked")}
        if self._sensor_key == "shadow_scorecard":
            return _as_dict(state.shadow_scorecard)
        if self._sensor_key == "mpc_plan":
            return _as_dict(state.mpc_plan)
        if self._sensor_key == "dynamic_tariff":
            return _as_dict(state.dynamic_tariff)
        if self._sensor_key in ("tou_shadow", "flex_event"):
            return _as_dict(getattr(state, self._sensor_key))
        if self._sensor_key == "upgrade_advisor":
            report = _as_dict(state.upgrade_advisor)
            options = upgrade_options(report)
            return {
                "date": report.get("date"), "days": report.get("days"), "months": report.get("months"),
                "best_option": options[0]["name"] if options else None, "options": options,
            }
        if self._sensor_key == "load_profile":
            return _as_dict(state.load_profile)
        if self._sensor_key == "solar_forecast_p50":
            plan = _as_dict(state.last_import_plan)
            return {**_as_dict(plan.get("forecast_quantiles")), "date": plan.get("date"),
                    "raw_forecast_kwh": plan.get("raw_forecast_kwh")}
        if self._sensor_key == "pv_strings":
            return pv_string_attributes(state.pv_strings)
        if self._sensor_key == "battery_runtime":
            return {k: v for k, v in _as_dict(state.battery_runtime).items() if k != "hours"}
        if self._sensor_key == "api_success_rate":
            return {k: v for k, v in _as_dict(state.api_health).items() if k not in ("type", "success_rate_pct")}
        if self._sensor_key == "health":
            return {k: v for k, v in _as_dict(state.daily_health).items() if k not in ("type", "status")}
        if self._sensor_key == "data_health":
            return _as_dict(_as_dict(state.data_health).get("classes"))
        if self._sensor_key == "import_causes":
            return {k: v for k, v in _as_dict(state.import_causes).items() if k != "total_kwh"}
        if self._sensor_key == "marginal_cost":
            cost = _as_dict(state.marginal_cost)
            cheapest = _as_dict(cost.get("cheapest_hour_today"))
            return {
                "source": cost.get("source"),
                "cheapest_hour_today": cheapest.get("hour"),
                "cheapest_hour_pence": cheapest.get("pence"),
                "cheapest_hour_source": cheapest.get("source"),
                "reserve_soc": cost.get("reserve_soc"),
            }
        if self._sensor_key == "last_error":
            attrs: dict[str, Any] = {}
            if state.last_error and clip_text(state.last_error) != state.last_error:
                attrs["full_error"] = state.last_error
            if state.last_api_result:
                attrs["last_api_result"] = state.last_api_result
            if state.last_notification:
                attrs["last_notification"] = state.last_notification
            return attrs
        if self._sensor_key == "consumption":
            # Merges the capture points (06:00 morning, 22:00 actuals,
            # 16:00-19:00 peak window, 06:00 settled daily cost, and the
            # running year-to-date cost) into one attribute set — their field
            # names don't collide (peak_*/year_to_date_* are distinctly
            # prefixed). daily_cost's own `date` is exposed separately since,
            # unlike the others, it's inherently yesterday's data (Octopus
            # settlement lag) rather than today's.
            morning = _as_dict(state.last_morning_state)
            day = _as_dict(state.last_day_actuals)
            peak = _as_dict(state.last_peak_window_usage)
            cost = _as_dict(state.last_daily_cost)
            ytd = _as_dict(state.last_year_to_date_cost)
            attrs = {}
            if "overnight_load_kwh" in morning:
                attrs["overnight_load_kwh"] = morning["overnight_load_kwh"]
            attrs.update({k: v for k, v in day.items() if k not in ("type", "date")})
            attrs.update({k: v for k, v in peak.items() if k not in ("type", "date")})
            if cost:
                attrs["daily_cost_date"] = cost.get("date")
                attrs.update({k: v for k, v in cost.items() if k not in ("type", "date")})
            if ytd:
                attrs["year_to_date_net_cost_gbp"] = ytd.get("net_cost_gbp")
                attrs["year_to_date_year"] = ytd.get("year")
                attrs["year_to_date_days_counted"] = ytd.get("days_counted")
            return attrs

        # Adaptive learning sensors expose calibration progress so the user can
        # see when a correction will activate (days_collected vs days_required).
        if self._sensor_key in _ADAPTIVE_THRESHOLDS:
            days_required = _ADAPTIVE_THRESHOLDS[self._sensor_key]
            days_key = _ADAPTIVE_PLAN_KEYS[self._sensor_key][2]
            days_collected = _as_dict(state.last_import_plan).get(days_key, 0) or 0
            return {
                "days_collected": days_collected,
                "days_required": days_required,
                "active": days_collected >= days_required,
            }
        return {}
