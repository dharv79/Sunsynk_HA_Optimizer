# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Sensors for Sunsynk Optimizer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


_ADAPTIVE_THRESHOLDS = {
    "forecast_correction": 7,        # _MIN_DAYS_FORECAST_CORRECTION
    "overnight_drain_adjustment": 5,  # _MIN_DAYS_SOC_ADJUSTMENT
    "evening_soc_adjustment": 5,      # _MIN_DAYS_SOC_ADJUSTMENT
    "effective_charge_rate": 3,       # min for compute_effective_charge_rate_kw
}


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
            SunsynkOptimizerSensor(coordinator, entry, "import_plan_end", "Import plan end"),
            SunsynkOptimizerSensor(coordinator, entry, "flux2_action", "Flux 2 action"),
            SunsynkOptimizerSensor(coordinator, entry, "last_error", "Last error"),
            SunsynkOptimizerSensor(coordinator, entry, "last_updated", "Last updated"),
            SunsynkOptimizerSensor(coordinator, entry, "next_import_window", "Next import window"),
            SunsynkOptimizerSensor(coordinator, entry, "current_soc_target", "Current SOC target"),
            SunsynkOptimizerSensor(coordinator, entry, "operation_mode", "Operation mode"),
            SunsynkOptimizerSensor(coordinator, entry, "forecast_correction", "Forecast correction"),
            SunsynkOptimizerSensor(coordinator, entry, "overnight_drain_adjustment", "Overnight drain adjustment"),
            SunsynkOptimizerSensor(coordinator, entry, "evening_soc_adjustment", "Evening SOC adjustment"),
            SunsynkOptimizerSensor(coordinator, entry, "effective_charge_rate", "Effective charge rate"),
        ]
    )


class SunsynkOptimizerSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, sensor_key: str, name: str) -> None:
        super().__init__(coordinator)
        self._sensor_key = sensor_key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{sensor_key}"

    @property
    def native_value(self) -> str | int | None:
        """Return the primary display value for this sensor.

        Each sensor_key maps to a specific field or derived string from OptimizerState.
        The import_plan_end and flux2_action sensors also expose rich extra_state_attributes.
        """
        state = self.coordinator.state

        if self._sensor_key == "selected_full_charge_day":
            return state.selected_full_charge_day
        if self._sensor_key == "operation_mode":
            return state.operation_mode
        if self._sensor_key == "current_soc_target":
            return state.current_soc_target
        if self._sensor_key == "next_import_window":
            return state.next_import_window
        if self._sensor_key == "last_error":
            return state.last_error or "OK"
        if self._sensor_key == "last_updated":
            return state.updated_at

        # Adaptive learning sensors — read from last_import_plan attributes.
        # Default values keep history continuous even before the first plan runs.
        if self._sensor_key in (
            "forecast_correction",
            "overnight_drain_adjustment",
            "evening_soc_adjustment",
            "effective_charge_rate",
        ):
            plan = state.last_import_plan if isinstance(state.last_import_plan, dict) else {}
            if self._sensor_key == "forecast_correction":
                return plan.get("forecast_correction_factor", 1.0)
            if self._sensor_key == "overnight_drain_adjustment":
                return plan.get("overnight_drain_adjustment", 0)
            if self._sensor_key == "evening_soc_adjustment":
                return plan.get("soc_adjustment", 0)
            if self._sensor_key == "effective_charge_rate":
                return plan.get("effective_charge_rate_kw")

        if self._sensor_key == "import_plan_end":
            plan = state.last_import_plan
            if not isinstance(plan, dict) or not plan:
                return None
            end = plan.get("flux1_end")
            target = plan.get("target_soc")
            if end and target is not None:
                return f"02:00→{end} target {target}%"
            return str(plan)

        if self._sensor_key == "flux2_action":
            action = state.last_flux2_action
            if not action:
                return None
            if isinstance(action, str):
                return action
            if not isinstance(action, dict):
                return str(action)

            name = action.get("action", "unknown")
            soc = action.get("soc")
            grid_pac = action.get("grid_pac")
            payload = action.get("payload")

            if name == "disable_evening_export":
                if grid_pac is not None:
                    return f"Export disabled, target 100% ({round(grid_pac)}W)"
                return "Export disabled, target 100%"
            if name == "trim_to_82":
                if isinstance(payload, dict):
                    flux2 = payload.get("flux_2", {})
                    end = flux2.get("endTime")
                    if end:
                        return f"Trim to 82% until {end}"
                if soc is not None:
                    return f"Trim to 82% (SOC {soc}%)"
                return "Trim to 82%"
            if name == "full_day_trim_to_82":
                return "Full charge trim to 82%"
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
            return state.last_import_plan
        if self._sensor_key == "flux2_action":
            action = state.last_flux2_action
            if isinstance(action, dict):
                return action
            if isinstance(action, str):
                return {"raw": action}
        if self._sensor_key == "selected_full_charge_day":
            return {"scores": state.last_full_charge_scores}
        if self._sensor_key == "last_error":
            attrs: dict[str, Any] = {}
            if state.last_api_result:
                attrs["last_api_result"] = state.last_api_result
            if state.last_notification:
                attrs["last_notification"] = state.last_notification
            return attrs

        # Adaptive learning sensors expose calibration progress so the user can
        # see when a correction will activate (days_collected vs days_required).
        if self._sensor_key in _ADAPTIVE_THRESHOLDS:
            plan = state.last_import_plan if isinstance(state.last_import_plan, dict) else {}
            days_required = _ADAPTIVE_THRESHOLDS[self._sensor_key]
            days_key = {
                "forecast_correction": "forecast_correction_days",
                "overnight_drain_adjustment": "overnight_drain_days",
                "evening_soc_adjustment": "soc_adjustment_days",
                "effective_charge_rate": "charge_rate_calibration_days",
            }[self._sensor_key]
            days_collected = plan.get(days_key, 0) or 0
            return {
                "days_collected": days_collected,
                "days_required": days_required,
                "active": days_collected >= days_required,
            }
        return {}
