# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Switches for Sunsynk Optimizer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Register optimizer switch entities for this config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AwayModeSwitch(coordinator, entry)])


class AwayModeSwitch(CoordinatorEntity, SwitchEntity):
    """User toggle for holiday / away mode.

    When on, the optimizer uses the away calibration profile (drain and
    evening-SOC nudge learned only from away days) and tags new days as away,
    so a low-load holiday can't skew the home learning. Persisted via the
    coordinator's OptimizerState, so it survives restarts.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "Away mode"
        self._attr_unique_id = f"{entry.entry_id}_away_mode"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.state.away_mode)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.update_state(away_mode=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.update_state(away_mode=False)
