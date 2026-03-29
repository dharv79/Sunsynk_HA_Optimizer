# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Helper functions for Sunsynk Optimizer payloads and defaults."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .const import (
    CONF_CHARGES,
    CONF_CURRENCY,
    CONF_FLUX_PRODUCTS,
    CONF_INVEST,
    CONF_NOTIFY_TARGET,
    CONF_OPERATION_MODE,
    CONF_PLANT_ID,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_OPERATION_MODE,
)


def default_charges() -> list[dict[str, Any]]:
    """Return default tariff rows based on the original working values."""
    return [
        {"price": 16.66, "type": "3", "startRange": "02:00", "endRange": "05:00", "status": "import"},
        {"price": 27.77, "type": "3", "startRange": "05:00", "endRange": "16:00", "status": "import"},
        {"price": 38.88, "type": "3", "startRange": "16:00", "endRange": "19:00", "status": "import"},
        {"price": 27.77, "type": "3", "startRange": "19:00", "endRange": "02:00", "status": "import"},
        {"price": 4.39, "type": "3", "startRange": "02:00", "endRange": "05:00", "status": "export"},
        {"price": 9.79, "type": "3", "startRange": "05:00", "endRange": "16:00", "status": "export"},
        {"price": 27.81, "type": "3", "startRange": "16:00", "endRange": "19:00", "status": "export"},
        {"price": 9.79, "type": "3", "startRange": "19:00", "endRange": "02:00", "status": "export"},
    ]


def default_flux_products() -> list[dict[str, Any]]:
    """Return default Flux windows based on the original working values."""
    return [
        {"provider": 2, "direction": 1, "startTime": "02:00", "endTime": "04:30", "targetSoc": 100},
        {"provider": 2, "direction": 0, "startTime": "16:00", "endTime": "16:15", "targetSoc": 85},
    ]


def merge_entry_data(data: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Merge config-entry data and options, with options overriding."""
    merged = deepcopy(data)
    merged.update(options)
    merged.setdefault(CONF_CHARGES, default_charges())
    merged.setdefault(CONF_FLUX_PRODUCTS, default_flux_products())
    merged.setdefault(CONF_NOTIFY_TARGET, DEFAULT_NOTIFY_TARGET)
    merged.setdefault(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE)
    return merged


def apply_flux_override(
    flux_products: list[dict[str, Any]],
    flux_1: dict[str, Any] | None = None,
    flux_2: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply Flux 1 / Flux 2 overrides to the configured defaults."""
    rows = deepcopy(flux_products) if flux_products else default_flux_products()
    if len(rows) < 2:
        rows = default_flux_products()

    if flux_1:
        rows[0].update(flux_1)
        rows[0].setdefault("provider", 2)
        rows[0].setdefault("direction", 1)
    if flux_2:
        rows[1].update(flux_2)
        rows[1].setdefault("provider", 2)
        rows[1].setdefault("direction", 0)
    return rows


def build_payload(config: dict[str, Any], flux_products: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the Sunsynk income payload."""
    return {
        "id": str(config[CONF_PLANT_ID]).strip(),
        "currency": int(config[CONF_CURRENCY]),
        "invest": int(config[CONF_INVEST]),
        "charges": deepcopy(config[CONF_CHARGES]),
        "fluxProducts": deepcopy(flux_products if flux_products is not None else config[CONF_FLUX_PRODUCTS]),
    }
