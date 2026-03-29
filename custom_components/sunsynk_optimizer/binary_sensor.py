# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Binary sensors for Sunsynk Optimizer."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
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
            SunsynkOptimizerBinarySensor(coordinator, entry, "evening_export_disabled", "Evening export disabled"),
            SunsynkOptimizerBinarySensor(coordinator, entry, "monitor_only", "Monitor only mode"),
        ]
    )


class SunsynkOptimizerBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def is_on(self) -> bool:
        state = self.coordinator.state
        if self._key == "evening_export_disabled":
            return bool(state.evening_export_disabled)
        if self._key == "monitor_only":
            return state.operation_mode == "monitor"
        return False
