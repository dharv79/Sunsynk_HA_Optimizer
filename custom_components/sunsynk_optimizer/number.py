# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Number entities for Sunsynk Optimizer (billed amount for bill reconciliation, phase 36)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Register the billed-amount entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BilledAmountEntity(coordinator, entry)])


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

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.state.bill_reconciliation or {}).get("billed_gbp")

    @property
    def extra_state_attributes(self) -> dict:
        state = self.coordinator.state.bill_reconciliation or {}
        return {"month": state.get("month"), **(state.get("result") or {})}

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.optimizer.async_set_billed_amount(round(float(value), 2))
