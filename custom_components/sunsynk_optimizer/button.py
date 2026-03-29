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
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SunsynkOptimizerButton(coordinator, entry, "choose_best_day", "Run choose best day"),
            SunsynkOptimizerButton(coordinator, entry, "run_import", "Run import plan"),
            SunsynkOptimizerButton(coordinator, entry, "run_flux2", "Run Flux 2 check"),
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
        if self._key == "choose_best_day":
            await self.coordinator.optimizer.async_choose_best_full_charge_day()
        elif self._key == "run_import":
            await self.coordinator.optimizer.async_run_import_plan()
        elif self._key == "run_flux2":
            await self.coordinator.optimizer.async_run_flux2_check()
        elif self._key == "reset_baseline":
            await self.coordinator.optimizer.async_reset_flux_baseline()
        elif self._key == "install_dashboard":
            await async_install_dashboard(self.coordinator.hass, self._entry)
