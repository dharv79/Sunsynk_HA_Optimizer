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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
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
        return {}
