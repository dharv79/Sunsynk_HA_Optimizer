"""Test bootstrap.

`data_logger.py` imports one Home Assistant name (`homeassistant.core.HomeAssistant`,
used only as a type hint). Home Assistant is not installed in the test/CI
environment, so we register a lightweight stub for it, then load `data_logger.py`
directly by file path — bypassing the package `__init__.py`, which would pull in
the full coordinator/optimizer HA-dependent import chain.

This lets the pure adaptive-learning logic be unit-tested with no running HA.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"


def _install_ha_stubs() -> None:
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    ha.core = core
    sys.modules.setdefault("homeassistant", ha)
    sys.modules.setdefault("homeassistant.core", core)


def _load_module(filename: str, modname: str):
    """Load a single integration source file in isolation (no package init)."""
    spec = importlib.util.spec_from_file_location(modname, _PKG / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_install_ha_stubs()
_data_logger = _load_module("data_logger.py", "sunsynk_data_logger")


@pytest.fixture(scope="session")
def DataLogger():
    """The DataLogger class, loaded without a running Home Assistant."""
    return _data_logger.DataLogger


@pytest.fixture
def dl():
    """A bare DataLogger instance (no __init__) for calling its pure methods.

    The adaptive-learning computations use only class constants and static
    helpers, so they run correctly on an instance created without hass/config.
    """
    return object.__new__(_data_logger.DataLogger)


def make_day(**overrides):
    """Build a paired-day dict with sensible defaults, overriding specific keys."""
    day = {
        "date": "2026-07-01",
        "solar_forecast_kwh": 20.0,
        "raw_forecast_kwh": 12.0,
        "actual_solar_kwh": 22.0,
        "forecast_band": "summer_like",
        "target_soc": 55,
        "initial_soc": 40,
        "morning_soc": 42,
        "morning_pv_power": 80.0,
        "overnight_drain_pct": 13.0,
        "evening_soc": 60.0,
        "evening_export_disabled": False,
        "is_full_day": False,
        "flux1_end": "02:30",
        "effective_charge_rate_kw": 1.2,
    }
    day.update(overrides)
    return day
