# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Binary sensors for Sunsynk Optimizer."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import optimizer_device_info, pin_entity_id
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register all optimizer binary sensor entities for this config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SunsynkOptimizerBinarySensor(
                coordinator, entry, "evening_export_disabled", "Evening export disabled",
                device_class=BinarySensorDeviceClass.RUNNING,
            ),
            SunsynkOptimizerBinarySensor(
                coordinator, entry, "grid_outage", "Grid outage",
                device_class=BinarySensorDeviceClass.PROBLEM, entity_id="binary_sensor.grid_outage",
            ),
            SunsynkOptimizerBinarySensor(
                coordinator, entry, "inverter_fault", "Inverter fault",
                device_class=BinarySensorDeviceClass.PROBLEM, entity_id="binary_sensor.inverter_fault",
            ),
            SunsynkOptimizerBinarySensor(
                coordinator, entry, "monitor_only", "Monitor only mode",
                icon="mdi:eye-outline",
            ),
        ]
    )


class SunsynkOptimizerBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        device_class: BinarySensorDeviceClass | None = None,
        icon: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        pin_entity_id(self, "binary_sensor", name, entity_id)
        self._attr_device_info = optimizer_device_info(entry)
        self._attr_device_class = device_class
        if icon:
            self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        """Return True when the flag this sensor represents is active."""
        state = self.coordinator.state
        if self._key == "evening_export_disabled":
            return bool(state.evening_export_disabled)
        if self._key == "grid_outage":
            return bool((state.grid_outage or {}).get("active"))
        if self._key == "inverter_fault":
            return bool((state.inverter_events or {}).get("fault"))
        if self._key == "monitor_only":
            return state.operation_mode == "monitor"
        return False

    @property
    def extra_state_attributes(self) -> dict:
        if self._key == "inverter_fault":
            events = self.coordinator.state.inverter_events or {}
            return {
                "events": events.get("active") or [], "counts": events.get("counts"), "checked": events.get("checked")
            }
        if self._key != "grid_outage":
            return {}
        outage = self.coordinator.state.grid_outage or {}
        return dict(outage if outage.get("active") else outage.get("last") or {})
