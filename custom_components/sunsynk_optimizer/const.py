# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Constants for Sunsynk Optimizer."""

from __future__ import annotations

DOMAIN = "sunsynk_optimizer"
PLATFORMS = ["sensor", "button", "binary_sensor"]
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_state"

CONF_WEATHER_ENTITY = "weather_entity"
CONF_SOLAR_FORECAST_SENSOR = "solar_forecast_sensor"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_TARGET = "notify_target"
CONF_EXPORT_DISABLE_THRESHOLD = "export_disable_threshold"
CONF_DEFAULT_FULL_CHARGE_DAY = "default_full_charge_day"
CONF_OPERATION_MODE = "operation_mode"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PLANT_ID = "plant_id"
CONF_INVERTER_SERIAL = "inverter_serial"
CONF_CURRENCY = "currency"
CONF_INVEST = "invest"
CONF_CHARGES = "charges"
CONF_FLUX_PRODUCTS = "flux_products"

DEFAULT_EXPORT_DISABLE_THRESHOLD = 1500  # watts; above this between 16–19h suggests heavy self-consumption, so export is counterproductive
DEFAULT_SOLAR_FORECAST_SENSOR = "sensor.energy_production_today"
DEFAULT_WEATHER_ENTITY = "weather.forecast_home"
DEFAULT_NOTIFY_SERVICE = "notify.notify"
DEFAULT_NOTIFY_TARGET = ""
DEFAULT_FULL_CHARGE_DAY = "Wednesday"
DEFAULT_CURRENCY = 366   # Sunsynk API internal currency code for GBP (not ISO 4217)
DEFAULT_INVEST = 9400    # default battery system cost in the above currency unit, used for ROI display in the Sunsynk portal
DEFAULT_OPERATION_MODE = "auto"
DEFAULT_BATTERY_CAPACITY = 10.0  # kWh — typical home battery system size
DEFAULT_CHARGE_RATE = 3.0        # kW — 0.3C rate typical for LiFePO4 systems
DEFAULT_AVG_CONSUMPTION_KW = 0.75    # kW — mid-range of typical 500–1000 W home load
DEFAULT_SOLAR_START_OFFSET_HOURS = 2.5  # hours after sunrise when solar covers home load

CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_CHARGE_RATE = "charge_rate_kw"
CONF_AVG_CONSUMPTION_KW = "avg_consumption_kw"
CONF_SOLAR_START_OFFSET_HOURS = "solar_start_offset_hours"

FULL_CHARGE_DAY_OPTIONS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
OPERATION_MODE_OPTIONS = ["auto", "monitor"]

SERVICE_RECALCULATE_FULL_CHARGE_DAY = "recalculate_full_charge_day"
SERVICE_RUN_IMPORT_PLAN_NOW = "run_import_plan_now"
SERVICE_RUN_FLUX2_CHECK_NOW = "run_flux2_check_now"
SERVICE_PUSH_CURRENT_CONFIG = "push_current_config"
SERVICE_PUSH_FLUX_OVERRIDE = "push_flux_override"
SERVICE_RESET_FLUX_DEFAULTS = "reset_flux_defaults"
