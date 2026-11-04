# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Redacted debug bundle for the HA 'Download diagnostics' button (phase 45)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .flux_helpers import merge_entry_data
from .planning import redact_config

_PAIRED_DAYS = 7


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """State, the last 7 paired days and the merged config, with credentials and ids masked."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    paired = await coordinator.optimizer.data_logger.async_load_paired_days(days=_PAIRED_DAYS + 2)
    state = dict(coordinator.state.__dict__)
    return {
        "config": redact_config(merge_entry_data(dict(entry.data), dict(entry.options))),
        "state": redact_config(state),
        "paired_days": paired[-_PAIRED_DAYS:],
    }
