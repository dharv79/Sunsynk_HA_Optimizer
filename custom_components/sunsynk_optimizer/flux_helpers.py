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
from datetime import date, datetime, time, timedelta, tzinfo
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
    """Return default Flux windows based on the original working values.

    Index 0 is always Flux 1 (import, direction=1).
    Index 1 is always Flux 2 (export, direction=0).
    provider=2 is the Sunsynk API code for the Flux tariff product.
    These indices are fixed by the Sunsynk API — never reorder them.
    """
    return [
        {"provider": 2, "direction": 1, "startTime": "02:00", "endTime": "04:30", "targetSoc": 100},
        {"provider": 2, "direction": 0, "startTime": "16:00", "endTime": "16:15", "targetSoc": 85},
    ]


def _parse_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _range_midpoint_minutes(start: str, end: str) -> int:
    start_min, end_min = _parse_minutes(start), _parse_minutes(end)
    if end_min <= start_min:  # range wraps past midnight
        end_min += 24 * 60
    return (start_min + end_min) // 2 % (24 * 60)


def _minutes_in_range(minutes: int, start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    start_min, end_min = _parse_minutes(start), _parse_minutes(end)
    if end_min <= start_min:  # range wraps past midnight
        return minutes >= start_min or minutes < end_min
    return start_min <= minutes < end_min


def band_price_pence_per_kwh(
    charges: list[dict[str, Any]],
    window_start: str,
    window_end: str,
    status: str = "import",
) -> float | None:
    """Find the `status` ("import"/"export") price (pence/kWh) for a time window.

    Prefers an exact row matching window_start/window_end; if none matches
    exactly (e.g. the user has edited their charges to a different window
    shape), falls back to the row whose range contains the window's midpoint.
    Returns None if no row matches either way — callers must treat that as
    "no price", not as a price of zero.
    """
    rows = [row for row in charges if row.get("status") == status]
    for row in rows:
        if row.get("startRange") == window_start and row.get("endRange") == window_end:
            return float(row["price"])

    midpoint = _range_midpoint_minutes(window_start, window_end)
    for row in rows:
        if _minutes_in_range(midpoint, row.get("startRange"), row.get("endRange")):
            return float(row["price"])
    return None


def peak_import_price_pence_per_kwh(
    charges: list[dict[str, Any]],
    window_start: str = "16:00",
    window_end: str = "19:00",
) -> float | None:
    """Import price for the peak window; None when no import row matches (never 0p)."""
    return band_price_pence_per_kwh(charges, window_start, window_end, "import")


# Flux bands the KPIs and decisions price: key -> (start, end, status).
TARIFF_BANDS: dict[str, tuple[str, str, str]] = {
    "offpeak": ("02:00", "05:00", "import"),
    "day": ("05:00", "16:00", "import"),
    "peak": ("16:00", "19:00", "import"),
    "export_peak": ("16:00", "19:00", "export"),
}


def kpi_prices_pence(charges: list[dict[str, Any]]) -> dict[str, float | None]:
    """Prices the phase 14 KPIs need, keyed as `planning.day_kpis` expects."""
    return {
        key: band_price_pence_per_kwh(charges, start, end, status)
        for key, (start, end, status) in TARIFF_BANDS.items()
    }


def _rate_time(value: Any, tz: tzinfo | None) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if tz is not None and value.tzinfo is not None:
        value = value.astimezone(tz)
    return value


def _rate_slots(state: Any, attributes: dict[str, Any]) -> list[tuple[Any, Any, Any]]:
    """(start, end, £/kWh) slots from an Octopus Energy rates entity's attributes."""
    rows = attributes.get("rates") or attributes.get("all_rates")
    if isinstance(rows, list):
        return [
            (row.get("start"), row.get("end"), row.get("value_inc_vat"))
            for row in rows
            if isinstance(row, dict)
        ]
    # Current-rate sensor: one block, state is the rate in £/kWh.
    if attributes.get("start") is not None and attributes.get("end") is not None:
        return [(attributes["start"], attributes["end"], state)]
    return []


def octopus_rate_pence_per_kwh(
    state: Any,
    attributes: dict[str, Any] | None,
    window_start: str,
    window_end: str,
    on_date: date | None = None,
    tz: tzinfo | None = None,
) -> float | None:
    """Time-weighted p/kWh for an HH:MM window from an Octopus Energy rates entity (phase 18).

    Accepts the Octopus Energy integration's day-rates event (`rates` list),
    the legacy `all_rates` attribute, or the current-rate sensor (`start`/`end`
    attributes, state = rate). Rates are £/kWh inc VAT, returned in pence.
    Uses `on_date`'s window when the slots cover it, else the latest covered
    date (Flux rates repeat daily). None when no slot overlaps — never 0p.
    """
    start_min, end_min = _parse_minutes(window_start), _parse_minutes(window_end)
    if end_min <= start_min:  # window wraps past midnight
        end_min += 24 * 60
    covered: dict[date, list[float]] = {}  # window date -> [seconds, pence * seconds]
    for raw_start, raw_end, raw_value in _rate_slots(state, attributes or {}):
        slot_start, slot_end = _rate_time(raw_start, tz), _rate_time(raw_end, tz)
        try:
            pence = float(raw_value) * 100
        except (TypeError, ValueError):
            continue
        if slot_start is None or slot_end is None or slot_end <= slot_start:
            continue
        for day in (slot_start.date() - timedelta(days=1), slot_start.date()):
            midnight = datetime.combine(day, time(), tzinfo=slot_start.tzinfo)
            overlap = (
                min(slot_end, midnight + timedelta(minutes=end_min))
                - max(slot_start, midnight + timedelta(minutes=start_min))
            ).total_seconds()
            if overlap > 0:
                totals = covered.setdefault(day, [0.0, 0.0])
                totals[0] += overlap
                totals[1] += pence * overlap
    if not covered:
        return None
    seconds, weighted = covered[on_date if on_date in covered else max(covered)]
    return round(weighted / seconds, 3)


def tariff_prices_pence(
    charges: list[dict[str, Any]],
    octopus: dict[str, float | None] | None = None,
) -> tuple[dict[str, float | None], dict[str, str | None]]:
    """Band prices (as `kpi_prices_pence`) preferring Octopus-entity rates (phase 18).

    `octopus` maps band key -> p/kWh read from the rates entity (None/absent
    = unavailable). Returns (prices, sources); source per band is "octopus",
    "charges", or None when neither has a price.
    """
    prices = kpi_prices_pence(charges)
    sources: dict[str, str | None] = {key: None if value is None else "charges" for key, value in prices.items()}
    for key, value in (octopus or {}).items():
        if key in prices and value is not None:
            prices[key], sources[key] = value, "octopus"
    return prices, sources


def price_source(sources: dict[str, str | None], *keys: str) -> str | None:
    """One label for the bands a decision used: their common source, "mixed", or None."""
    used = {sources.get(key) for key in keys} - {None}
    if not used:
        return None
    return used.pop() if len(used) == 1 else "mixed"


def merge_entry_data(data: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Merge config-entry data and options, with options overriding.

    Always call this instead of reading entry.data or entry.options directly —
    settings may exist in either location depending on whether they were set
    at initial setup or via a later reconfiguration.
    """
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
    """Apply Flux 1 / Flux 2 overrides to the configured defaults.

    Provider and direction are preserved from the base config so callers only
    need to supply the fields they want to change (startTime, endTime, targetSoc).
    """
    rows = deepcopy(flux_products) if flux_products else default_flux_products()
    if len(rows) < 2:
        rows = default_flux_products()

    if flux_1:
        rows[0].update(flux_1)
        rows[0].setdefault("provider", 2)
        rows[0].setdefault("direction", 1)  # direction=1 → import
    if flux_2:
        rows[1].update(flux_2)
        rows[1].setdefault("provider", 2)
        rows[1].setdefault("direction", 0)  # direction=0 → export
    return rows


def build_payload(config: dict[str, Any], flux_products: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the full Sunsynk income POST body from config and optional flux override."""
    return {
        "id": str(config[CONF_PLANT_ID]).strip(),
        "currency": int(config[CONF_CURRENCY]),
        "invest": int(config[CONF_INVEST]),
        "charges": deepcopy(config[CONF_CHARGES]),
        "fluxProducts": deepcopy(flux_products if flux_products is not None else config[CONF_FLUX_PRODUCTS]),
    }
