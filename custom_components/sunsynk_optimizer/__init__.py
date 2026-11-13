# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""The Sunsynk Optimizer integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .card_resource import async_register_card
from .const import DOMAIN, PLATFORMS, REMOVED_ENTITY_KEYS
from .coordinator import SunsynkOptimizerCoordinator


def _optimizer(hass: HomeAssistant):
    """The single config entry's optimizer (the integration is single_config_entry)."""
    coordinators = [c for c in hass.data.get(DOMAIN, {}).values() if hasattr(c, "optimizer")]
    if not coordinators:
        raise HomeAssistantError("Sunsynk Optimizer is not set up")
    return coordinators[0].optimizer


@websocket_api.websocket_command({
    vol.Required("type"): "sunsynk_optimizer/heatmap",
    vol.Optional("kind", default="import"): vol.In(("import", "export", "load")),
    vol.Optional("days", default=28): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
})
@websocket_api.async_response
async def _ws_heatmap(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Half-hourly import / export / load for the heat-map card (phase 106); read-only."""
    try:
        data = await _optimizer(hass).async_heatmap_data(msg["kind"], msg["days"])
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "not_ready", str(err))
        return
    connection.send_result(msg["id"], data)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    websocket_api.async_register_command(hass, _ws_heatmap)
    await async_register_card(hass)

    async def _set_target(call: ServiceCall) -> None:
        await _optimizer(hass).async_service_override(soc=call.data["soc"])

    async def _skip(call: ServiceCall) -> None:
        await _optimizer(hass).async_service_override(skip=True)

    async def _schedule(call: ServiceCall) -> None:
        await _optimizer(hass).async_service_schedule_free_event(call.data["start"], call.data["end"])

    async def _cancel(call: ServiceCall) -> None:
        await _optimizer(hass).async_service_cancel_free_event()

    async def _get_plan(call: ServiceCall) -> dict:
        return await _optimizer(hass).async_service_get_plan()

    hass.services.async_register(
        DOMAIN, "set_tonight_target", _set_target,
        schema=vol.Schema({vol.Required("soc"): vol.All(vol.Coerce(int), vol.Range(min=20, max=100))}),
    )
    hass.services.async_register(DOMAIN, "skip_tonight", _skip, schema=vol.Schema({}))
    hass.services.async_register(
        DOMAIN, "schedule_free_event", _schedule,
        schema=vol.Schema({vol.Required("start"): cv.datetime, vol.Required("end"): cv.datetime}),
    )
    async def _probe(call: ServiceCall) -> dict:
        return await _optimizer(hass).async_service_probe_events()

    hass.services.async_register(
        DOMAIN, "probe_inverter_events", _probe, schema=vol.Schema({}), supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(DOMAIN, "cancel_free_event", _cancel, schema=vol.Schema({}))
    async def _export(call: ServiceCall) -> dict:
        start, end = call.data.get("start"), call.data.get("end")
        return await _optimizer(hass).async_service_export_data(
            start.isoformat() if start else None, end.isoformat() if end else None, call.data.get("types")
        )

    hass.services.async_register(
        DOMAIN, "export_data", _export,
        schema=vol.Schema({
            vol.Optional("start"): cv.date,
            vol.Optional("end"): cv.date,
            vol.Optional("types"): vol.All(cv.ensure_list, [cv.string]),
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "get_plan", _get_plan, schema=vol.Schema({}), supports_response=SupportsResponse.ONLY
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = SunsynkOptimizerCoordinator(hass, entry)
    await coordinator.async_initialize()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    _remove_stale_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop registry entries for entities a removed feature left behind (saving sessions)."""
    stale = {f"{entry.entry_id}_{key}" for key in REMOVED_ENTITY_KEYS}
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.unique_id in stale:
            registry.async_remove(reg_entry.entity_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # Let HA run the full unload (incl. entry.async_on_unload callbacks) so
    # listeners don't stack up across options saves.
    await hass.config_entries.async_reload(entry.entry_id)
