"""Config flow for Sunsynk Optimizer."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SunsynkApiClient
from .const import (
    CONF_CHARGES,
    CONF_CURRENCY,
    CONF_DEFAULT_FULL_CHARGE_DAY,
    CONF_EXPORT_DISABLE_THRESHOLD,
    CONF_FLUX_PRODUCTS,
    CONF_INVEST,
    CONF_INVERTER_SERIAL,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TARGET,
    CONF_OPERATION_MODE,
    CONF_PASSWORD,
    CONF_PLANT_ID,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_USERNAME,
    CONF_WEATHER_ENTITY,
    DEFAULT_CURRENCY,
    DEFAULT_EXPORT_DISABLE_THRESHOLD,
    DEFAULT_FULL_CHARGE_DAY,
    DEFAULT_INVEST,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SOLAR_FORECAST_SENSOR,
    DEFAULT_WEATHER_ENTITY,
    DOMAIN,
    FULL_CHARGE_DAY_OPTIONS,
    OPERATION_MODE_OPTIONS,
)
from .flux_helpers import default_charges, default_flux_products, merge_entry_data

_LOGGER = logging.getLogger(__name__)
STATUS_OPTIONS = ["import", "export"]


def _time_selector() -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT))


def _base_schema(values: dict[str, Any] | None = None) -> vol.Schema:
    values = values or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): selector.TextSelector(),
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_INVERTER_SERIAL, description={"suggested_value": values.get(CONF_INVERTER_SERIAL, "")}): selector.TextSelector(),
            vol.Required(CONF_PLANT_ID, description={"suggested_value": values.get(CONF_PLANT_ID, "")}): selector.TextSelector(),
            vol.Required(CONF_WEATHER_ENTITY, default=values.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Required(
                CONF_SOLAR_FORECAST_SENSOR,
                default=values.get(CONF_SOLAR_FORECAST_SENSOR, DEFAULT_SOLAR_FORECAST_SENSOR),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(CONF_NOTIFY_SERVICE, default=values.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE)): selector.TextSelector(),
            vol.Optional(CONF_NOTIFY_TARGET, default=values.get(CONF_NOTIFY_TARGET, DEFAULT_NOTIFY_TARGET)): selector.TextSelector(),
            vol.Required(CONF_OPERATION_MODE, default=values.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)): selector.SelectSelector(
                selector.SelectSelectorConfig(options=OPERATION_MODE_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_CURRENCY, default=values.get(CONF_CURRENCY, DEFAULT_CURRENCY)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=999, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_INVEST, default=values.get(CONF_INVEST, DEFAULT_INVEST)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1000000, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_EXPORT_DISABLE_THRESHOLD,
                default=values.get(CONF_EXPORT_DISABLE_THRESHOLD, DEFAULT_EXPORT_DISABLE_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=100, max=10000, step=100, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_DEFAULT_FULL_CHARGE_DAY,
                default=values.get(CONF_DEFAULT_FULL_CHARGE_DAY, DEFAULT_FULL_CHARGE_DAY),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(options=FULL_CHARGE_DAY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
            ),
        }
    )


def _charge_schema(charges: list[dict[str, Any]], start: int, end: int) -> vol.Schema:
    schema: dict[Any, Any] = {}
    for idx in range(start, end):
        entry = charges[idx]
        line = idx + 1
        schema[vol.Required(f"charge_{line}_price", default=float(entry["price"]))] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=1000, step=0.01, mode=selector.NumberSelectorMode.BOX)
        )
        schema[vol.Required(f"charge_{line}_start", default=str(entry["startRange"]))] = _time_selector()
        schema[vol.Required(f"charge_{line}_end", default=str(entry["endRange"]))] = _time_selector()
        schema[vol.Required(f"charge_{line}_status", default=str(entry["status"]))] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=STATUS_OPTIONS)
        )
    return vol.Schema(schema)


def _flux_schema(flux_products: list[dict[str, Any]]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("flux_1_start", default=str(flux_products[0]["startTime"])): _time_selector(),
            vol.Required("flux_1_end", default=str(flux_products[0]["endTime"])): _time_selector(),
            vol.Required("flux_1_target", default=int(flux_products[0]["targetSoc"])): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Required("flux_2_start", default=str(flux_products[1]["startTime"])): _time_selector(),
            vol.Required("flux_2_end", default=str(flux_products[1]["endTime"])): _time_selector(),
            vol.Required("flux_2_target", default=int(flux_products[1]["targetSoc"])): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }
    )


async def _validate_input(hass: HomeAssistant, user_input: dict[str, Any], validate_login: bool = True) -> dict[str, str]:
    errors: dict[str, str] = {}
    plant_id = str(user_input[CONF_PLANT_ID]).strip()
    inverter_serial = str(user_input[CONF_INVERTER_SERIAL]).strip()

    battery_soc_entity = f"sensor.solarsynkv3_{inverter_serial}_battery_soc"
    grid_pac_entity = f"sensor.solarsynkv3_{inverter_serial}_grid_pac"

    if not inverter_serial:
        errors[CONF_INVERTER_SERIAL] = "required"
    if not plant_id:
        errors[CONF_PLANT_ID] = "required"
    if hass.states.get(battery_soc_entity) is None:
        errors["base"] = "battery_soc_not_found"
    elif hass.states.get(grid_pac_entity) is None:
        errors["base"] = "grid_pac_not_found"

    if hass.states.get(user_input[CONF_WEATHER_ENTITY]) is None:
        errors[CONF_WEATHER_ENTITY] = "entity_not_found"
    if hass.states.get(user_input[CONF_SOLAR_FORECAST_SENSOR]) is None:
        errors[CONF_SOLAR_FORECAST_SENSOR] = "entity_not_found"

    notify_service = str(user_input[CONF_NOTIFY_SERVICE]).strip()
    if "." not in notify_service:
        errors[CONF_NOTIFY_SERVICE] = "invalid_service"
    else:
        domain, service = notify_service.split(".", 1)
        if not hass.services.has_service(domain, service):
            errors[CONF_NOTIFY_SERVICE] = "service_not_found"

    if validate_login:
        try:
            client = SunsynkApiClient(
                async_get_clientsession(hass),
                str(user_input[CONF_USERNAME]),
                str(user_input[CONF_PASSWORD]),
            )
            await client.async_login()
        except Exception as err:
            _LOGGER.exception("Sunsynk login validation failed: %s", err)
            errors["base"] = "cannot_connect"
    return errors


class SunsynkOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate_input(self.hass, user_input, validate_login=True)
            if not errors:
                data = dict(user_input)
                data.setdefault(CONF_NOTIFY_TARGET, DEFAULT_NOTIFY_TARGET)
                data.setdefault(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
                data.setdefault(CONF_CHARGES, default_charges())
                data.setdefault(CONF_FLUX_PRODUCTS, default_flux_products())
                title = f"Sunsynk Optimizer ({user_input[CONF_PLANT_ID]})"
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(step_id="user", data_schema=_base_schema(user_input), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SunsynkOptimizerOptionsFlow(config_entry)


class SunsynkOptimizerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._config_entry = config_entry
        self._working = merge_entry_data(dict(config_entry.data), dict(config_entry.options))

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            errors = await _validate_input(self.hass, user_input, validate_login=False)
            if not errors:
                self._working.update(user_input)
                self._working.setdefault(CONF_NOTIFY_TARGET, DEFAULT_NOTIFY_TARGET)
                self._working.setdefault(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
                return await self.async_step_charges_1()
            return self.async_show_form(step_id="init", data_schema=_base_schema(user_input), errors=errors)

        return self.async_show_form(step_id="init", data_schema=_base_schema(self._working))

    async def async_step_charges_1(self, user_input=None):
        if user_input is not None:
            self._save_charge_rows(user_input, 0, 4)
            return await self.async_step_charges_2()
        return self.async_show_form(step_id="charges_1", data_schema=_charge_schema(self._working[CONF_CHARGES], 0, 4))

    async def async_step_charges_2(self, user_input=None):
        if user_input is not None:
            self._save_charge_rows(user_input, 4, 8)
            return await self.async_step_flux()
        return self.async_show_form(step_id="charges_2", data_schema=_charge_schema(self._working[CONF_CHARGES], 4, 8))

    async def async_step_flux(self, user_input=None):
        if user_input is not None:
            self._working[CONF_FLUX_PRODUCTS] = [
                {"provider": 2, "direction": 1, "startTime": user_input["flux_1_start"], "endTime": user_input["flux_1_end"], "targetSoc": int(user_input["flux_1_target"])},
                {"provider": 2, "direction": 0, "startTime": user_input["flux_2_start"], "endTime": user_input["flux_2_end"], "targetSoc": int(user_input["flux_2_target"])},
            ]
            return self.async_create_entry(title="", data=self._working)
        return self.async_show_form(step_id="flux", data_schema=_flux_schema(self._working[CONF_FLUX_PRODUCTS]))

    def _save_charge_rows(self, user_input: dict[str, Any], start: int, end: int) -> None:
        charges = self._working[CONF_CHARGES]
        for idx in range(start, end):
            line = idx + 1
            charges[idx] = {
                "price": float(user_input[f"charge_{line}_price"]),
                "type": "3",
                "startRange": user_input[f"charge_{line}_start"],
                "endRange": user_input[f"charge_{line}_end"],
                "status": user_input[f"charge_{line}_status"],
            }
