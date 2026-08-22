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
PLATFORMS = ["sensor", "button", "binary_sensor", "switch"]
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
DEFAULT_AVG_CONSUMPTION_KW = 0.75        # kW — mid-range of typical 500–1000 W home load
DEFAULT_WEEKEND_AVG_CONSUMPTION_KW = 0.9 # kW — slightly higher: people home during the day
DEFAULT_AWAY_AVG_CONSUMPTION_KW = 0.3    # kW — holiday base load only (fridge, standby)
DEFAULT_SOLAR_START_OFFSET_HOURS = 2.5  # hours after sunrise when solar covers home load

CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_CHARGE_RATE = "charge_rate_kw"
CONF_AVG_CONSUMPTION_KW = "avg_consumption_kw"
CONF_WEEKEND_AVG_CONSUMPTION_KW = "weekend_avg_consumption_kw"
CONF_AWAY_AVG_CONSUMPTION_KW = "away_avg_consumption_kw"
CONF_SOLAR_START_OFFSET_HOURS = "solar_start_offset_hours"
CONF_HOURLY_FORECAST_SENSOR = "hourly_forecast_sensor"
CONF_HOURLY_FORECAST_ATTRIBUTE = "hourly_forecast_attribute"
DEFAULT_HOURLY_FORECAST_ATTRIBUTE = "hourly"

FULL_CHARGE_DAY_OPTIONS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
OPERATION_MODE_OPTIONS = ["auto", "monitor"]

CONF_DATA_REPORT_TARGET = "data_report_target"

# Optional link to the separately-installed Home Assistant Octopus Energy
# integration. Free text (not EntitySelector) because Octopus entity IDs carry
# an unpredictable MPAN/MPRN/serial suffix the user must copy from their own
# install. Point at the "previous accumulative cost" sensors, which settle to
# reflect the prior calendar day a few hours after midnight.
CONF_OCTOPUS_IMPORT_COST_SENSOR = "octopus_import_cost_sensor"
CONF_OCTOPUS_EXPORT_INCOME_SENSOR = "octopus_export_income_sensor"
CONF_OCTOPUS_GAS_COST_SENSOR = "octopus_gas_cost_sensor"

# Cost-aware export-disable threshold (v1.0.11 Part 3). Ships in shadow mode
# by default — the cost trigger is computed and logged alongside the existing
# Watt trigger, but the real decision keeps using the Watt trigger until the
# user explicitly turns shadow mode off after reviewing a few weeks of data.
CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR = "export_disable_cost_threshold_pence_per_hour"
CONF_COST_AWARE_EXPORT_SHADOW_MODE = "cost_aware_export_shadow_mode"
# = 1.5 kW (DEFAULT_EXPORT_DISABLE_THRESHOLD) x 38.88 p/kWh (default 16:00-19:00
# import price) — back-computed so a fresh upgrade is behaviour-neutral even if
# shadow mode is later turned off without the user changing this default.
DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR = 58.32
DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE = True

# Optional HA AI Task-generated weekly insight (v1.0.11 Part 4). Off by
# default: depends on the user having an AI Task provider configured in this
# HA instance (Settings -> Voice assistants), which may incur cost on their
# own configured LLM. Graceful-degrade like every other optional integration
# in this component — checked at call time, not assumed from this toggle alone.
CONF_ENABLE_AI_WEEKLY_INSIGHT = "enable_ai_weekly_insight"
DEFAULT_ENABLE_AI_WEEKLY_INSIGHT = False

SERVICE_RECALCULATE_FULL_CHARGE_DAY = "recalculate_full_charge_day"
SERVICE_RUN_IMPORT_PLAN_NOW = "run_import_plan_now"
SERVICE_RUN_FLUX2_CHECK_NOW = "run_flux2_check_now"
SERVICE_PUSH_CURRENT_CONFIG = "push_current_config"
SERVICE_PUSH_FLUX_OVERRIDE = "push_flux_override"
SERVICE_RESET_FLUX_DEFAULTS = "reset_flux_defaults"
