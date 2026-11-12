# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared device info so every platform's entities group under one device (phase 47)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .flux_helpers import merge_entry_data
from .planning import entity_slug

# unique_id -> the entity id the generated dashboard uses (phase 94). Filled as entities are
# created; the dashboard installer maps these to the ids the entity registry really holds.
DEFAULT_ENTITY_IDS: dict[str, str] = {}


def pin_entity_id(entity, domain: str, name: str, entity_id: str | None = None) -> None:
    """Give a new entity the id the dashboard references, not "<domain>.sunsynk_optimizer_<name>".

    Only a new registration uses it: the entity registry keeps the id of an existing entity.
    Call after the unique id is set.
    """
    entity.entity_id = entity_id or f"{domain}.{entity_slug(name)}"
    DEFAULT_ENTITY_IDS[entity.unique_id] = entity.entity_id


def optimizer_device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device per inverter, keyed on the inverter serial (rule 2: not plant_id)."""
    serial = str(merge_entry_data(dict(entry.data), dict(entry.options)).get("inverter_serial", entry.entry_id))
    return DeviceInfo(
        identifiers={(DOMAIN, serial.strip() or entry.entry_id)},
        name="Sunsynk Optimizer",
        manufacturer="Sunsynk",
        model="Inverter optimizer",
    )
