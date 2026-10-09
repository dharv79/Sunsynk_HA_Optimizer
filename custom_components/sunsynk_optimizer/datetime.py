# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Datetime entities for Sunsynk Optimizer (manual free-event and saving-session entry)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register the manual free-electricity-event start/end entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            FreeEventTimeEntity(coordinator, entry, "start", "Free event start"),
            FreeEventTimeEntity(coordinator, entry, "end", "Free event end"),
            SavingSessionTimeEntity(coordinator, entry, "start", "Saving session start"),
            SavingSessionTimeEntity(coordinator, entry, "end", "Saving session end"),
        ]
    )


class FreeEventTimeEntity(CoordinatorEntity, DateTimeEntity):
    """Manual start/end entry for a free-electricity event.

    Setting both fields (end after start, start in the future) schedules the
    event immediately via SunsynkOptimizer.async_try_schedule_manual_free_event.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:flash-alert"

    def __init__(self, coordinator, entry: ConfigEntry, which: str, name: str) -> None:
        super().__init__(coordinator)
        self._which = which
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_free_event_{which}"

    @property
    def _state_field(self) -> str:
        return "free_event_manual_start" if self._which == "start" else "free_event_manual_end"

    @property
    def native_value(self) -> datetime | None:
        raw = getattr(self.coordinator.state, self._state_field)
        return dt_util.parse_datetime(raw) if raw else None

    async def async_set_value(self, value: datetime) -> None:
        self.coordinator.update_state(**{self._state_field: value.isoformat()})
        await self.coordinator.optimizer.async_try_schedule_manual_free_event(self._which)


class SavingSessionTimeEntity(FreeEventTimeEntity):
    """Manual start/end entry for an Octopus Saving Session (phase 21)."""

    _attr_icon = "mdi:piggy-bank-outline"

    def __init__(self, coordinator, entry: ConfigEntry, which: str, name: str) -> None:
        super().__init__(coordinator, entry, which, name)
        self._attr_unique_id = f"{entry.entry_id}_saving_session_{which}"

    @property
    def _state_field(self) -> str:
        return "saving_session_manual_start" if self._which == "start" else "saving_session_manual_end"

    async def async_set_value(self, value: datetime) -> None:
        self.coordinator.update_state(**{self._state_field: value.isoformat()})
        await self.coordinator.optimizer.async_try_schedule_manual_saving_session(self._which)
