"""Coordinator for Sunsynk Optimizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import SunsynkApiClient
from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION, CONF_PASSWORD, CONF_USERNAME
from .flux_helpers import merge_entry_data
from .optimizer import SunsynkOptimizer


@dataclass
class OptimizerState:
    selected_full_charge_day: str | None = None
    last_full_charge_scores: dict[str, float] = field(default_factory=dict)
    last_import_plan: dict[str, Any] = field(default_factory=dict)
    last_flux2_action: dict[str, Any] = field(default_factory=dict)
    evening_export_disabled: bool = False
    updated_at: str | None = None
    last_error: str | None = None
    last_notification: dict[str, Any] = field(default_factory=dict)
    last_api_result: dict[str, Any] = field(default_factory=dict)
    next_import_window: str | None = None
    current_soc_target: int | None = None
    operation_mode: str = "auto"
    last_payload_hash: str | None = None


class SunsynkOptimizerCoordinator(DataUpdateCoordinator[OptimizerState]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__package__),
            name="Sunsynk Optimizer",
        )
        self.entry = entry
        self.storage = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
        merged = merge_entry_data(dict(entry.data), dict(entry.options))
        self.api = SunsynkApiClient(async_get_clientsession(hass), merged[CONF_USERNAME], merged[CONF_PASSWORD])
        self.optimizer = SunsynkOptimizer(hass, entry, self)
        self.state = OptimizerState()

    async def async_initialize(self) -> None:
        stored = await self.storage.async_load() or {}
        for key, value in stored.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        merged = merge_entry_data(dict(self.entry.data), dict(self.entry.options))
        self.state.operation_mode = merged.get("operation_mode", "auto")
        await self.optimizer.async_setup()
        self.async_set_updated_data(self.state)

    async def async_shutdown(self) -> None:
        await self.optimizer.async_shutdown()

    def update_state(self, touch: bool = True, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
        if touch:
            self.state.updated_at = datetime.now(timezone.utc).isoformat()
        self.async_set_updated_data(self.state)
        self.hass.async_create_task(self.storage.async_save(self.state.__dict__))
