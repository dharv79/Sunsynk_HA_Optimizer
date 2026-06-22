# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Buttons for Sunsynk Optimizer."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .dashboard_installer import async_install_dashboard


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register all optimizer button entities for this config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SunsynkOptimizerButton(coordinator, entry, "choose_best_day", "Run choose best day"),
            SunsynkOptimizerButton(coordinator, entry, "test_plan", "Test plan"),
            SunsynkOptimizerButton(coordinator, entry, "reset_baseline", "Reset to baseline"),
            SunsynkOptimizerButton(coordinator, entry, "install_dashboard", "Install dashboard"),
        ]
    )


class SunsynkOptimizerButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    async def async_press(self) -> None:
        """Dispatch button press to the matching optimizer method."""
        if self._key == "choose_best_day":
            await self.coordinator.optimizer.async_choose_best_full_charge_day()
        elif self._key == "test_plan":
            await self.coordinator.optimizer.async_run_import_plan(source="test_button", dry_run=True)
        elif self._key == "reset_baseline":
            await self.coordinator.optimizer.async_reset_flux_baseline()
        elif self._key == "install_dashboard":
            await async_install_dashboard(self.coordinator.hass, self._entry)
