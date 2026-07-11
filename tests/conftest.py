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


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _install_ha_stubs() -> None:
    ha = _stub("homeassistant")
    ha.core = _stub("homeassistant.core", HomeAssistant=object)
    ha.config_entries = _stub("homeassistant.config_entries", ConfigEntry=object)
    components = _stub("homeassistant.components")
    components.persistent_notification = _stub(
        "homeassistant.components.persistent_notification", async_create=lambda *a, **k: None
    )
    # Bare package for sunsynk_optimizer so relative imports (from .const import …)
    # resolve to the submodules we register below — without running __init__.py
    # (which would pull in the full coordinator/optimizer HA import chain).
    pkg = _stub("sunsynk_optimizer")
    pkg.__path__ = [str(_PKG)]


def _load_module(filename: str, modname: str):
    """Load a single integration source file as sunsynk_optimizer.<modname>."""
    spec = importlib.util.spec_from_file_location(f"sunsynk_optimizer.{modname}", _PKG / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"sunsynk_optimizer.{modname}"] = module
    spec.loader.exec_module(module)
    return module


_install_ha_stubs()
_load_module("const.py", "const")
_load_module("flux_helpers.py", "flux_helpers")
_data_logger = _load_module("data_logger.py", "data_logger")
_dashboard = _load_module("dashboard_installer.py", "dashboard_installer")


@pytest.fixture(scope="session")
def DataLogger():
    """The DataLogger class, loaded without a running Home Assistant."""
    return _data_logger.DataLogger


@pytest.fixture(scope="session")
def safe_id():
    """dashboard_installer._safe_id, loaded without a running Home Assistant."""
    return _dashboard._safe_id


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
