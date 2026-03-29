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

DEFAULT_EXPORT_DISABLE_THRESHOLD = 1500
DEFAULT_SOLAR_FORECAST_SENSOR = "sensor.energy_production_today"
DEFAULT_WEATHER_ENTITY = "weather.forecast_home"
DEFAULT_NOTIFY_SERVICE = "notify.notify"
DEFAULT_NOTIFY_TARGET = ""
DEFAULT_FULL_CHARGE_DAY = "Wednesday"
DEFAULT_CURRENCY = 366
DEFAULT_INVEST = 9400
DEFAULT_OPERATION_MODE = "auto"

FULL_CHARGE_DAY_OPTIONS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
OPERATION_MODE_OPTIONS = ["auto", "monitor"]

SERVICE_RECALCULATE_FULL_CHARGE_DAY = "recalculate_full_charge_day"
SERVICE_RUN_IMPORT_PLAN_NOW = "run_import_plan_now"
SERVICE_RUN_FLUX2_CHECK_NOW = "run_flux2_check_now"
SERVICE_PUSH_CURRENT_CONFIG = "push_current_config"
SERVICE_PUSH_FLUX_OVERRIDE = "push_flux_override"
SERVICE_RESET_FLUX_DEFAULTS = "reset_flux_defaults"
