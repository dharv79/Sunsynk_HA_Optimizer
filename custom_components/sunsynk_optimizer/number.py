# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Number entities for Sunsynk Optimizer (billed amount phase 36, payback opening phase 119, peak minimum SOC phase 122)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import optimizer_device_info, pin_entity_id
from .const import DOMAIN
from .planning import bill_attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the billed-amount entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [BilledAmountEntity(coordinator, entry), PaybackOpeningEntity(coordinator, entry), PeakMinSocEntity(coordinator, entry)]
    )


class BilledAmountEntity(CoordinatorEntity, NumberEntity):
    """Last month's billed net £ from the Octopus statement.

    Setting it reconciles last calendar month's logged cost against it via
    SunsynkOptimizer.async_set_billed_amount.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:receipt-text-outline"
    _attr_name = "Billed last month"
    _attr_native_unit_of_measurement = "£"
    _attr_native_min_value = -1000.0
    _attr_native_max_value = 10000.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_billed_last_month"
        self._attr_device_info = optimizer_device_info(entry)
        pin_entity_id(self, "number", "Billed last month")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.state.bill_reconciliation or {}).get("billed_gbp")

    @property
    def extra_state_attributes(self) -> dict:
        # Fixed key set so the dashboard's attribute rows exist before the first bill (Spook).
        return bill_attributes(self.coordinator.state.bill_reconciliation)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.optimizer.async_set_billed_amount(round(float(value), 2))


class PaybackOpeningEntity(CoordinatorEntity, NumberEntity):
    """£ saved before the savings ledger began (June 2025 to 9 Oct 2026), counted towards the payback (phase 119)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cash-plus"
    _attr_name = "Saved before tracking"
    _attr_native_unit_of_measurement = "£"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100000.0
    _attr_native_step = 0.01
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_payback_opening"
        self._attr_device_info = optimizer_device_info(entry)
        pin_entity_id(self, "number", "Saved before tracking")

    @property
    def native_value(self) -> float:
        return float(self.coordinator.state.payback_opening_gbp or 0.0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.update_state(touch=False, payback_opening_gbp=round(float(value), 2))


class PeakMinSocEntity(CoordinatorEntity, NumberEntity):
    """Lowest SOC the 14:00 peak top-up/hold aims for at 16:00 (phase 122); 0 leaves it to the load estimate."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-arrow-up-outline"
    _attr_name = "Peak minimum SOC"
    _attr_native_unit_of_measurement = "%"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_peak_min_soc"
        self._attr_device_info = optimizer_device_info(entry)
        pin_entity_id(self, "number", "Peak minimum SOC")

    @property
    def native_value(self) -> int:
        return int(self.coordinator.state.peak_min_soc or 0)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.update_state(touch=False, peak_min_soc=int(round(float(value))))
