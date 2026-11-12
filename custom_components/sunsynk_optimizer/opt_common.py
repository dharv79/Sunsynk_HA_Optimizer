# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Small formatting helpers and constants shared by the optimizer modules."""

from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
)
from typing import Any

# How many days back an Octopus "previous accumulative cost" reading is still
# accepted when its settlement is running behind schedule (see
# SunsynkOptimizer._read_octopus_previous_day_cost).
_OCTOPUS_CATCH_UP_DAYS = 5

def _octopus_oldest_date(now: datetime) -> str:
    """ISO date of the oldest day an Octopus reading is still accepted for."""
    return (now - timedelta(days=_OCTOPUS_CATCH_UP_DAYS)).date().isoformat()

_UNAVAILABLE_STATES = ("unknown", "unavailable", "none", "")

_INITIAL_REFRESH_MAX_RETRIES = 5  # 60 s apart: covers slow first poll after restart

def _api_note(api_ok: bool) -> str:
    return "" if api_ok else " (inverter NOT updated)"

_DRIFT_SLOT_NAMES = {1: "Flux 1 (import)", 2: "Flux 2 (export)"}

def _drift_line(diff: dict[str, Any]) -> str:
    """One human line per drift item: "Flux 1 (import) targetSoc: plan 85, inverter 100"."""
    if diff["slot"] is None:
        return f"Grid charge current: plan {diff['expected']} A, inverter {diff['actual']} A"
    name = _DRIFT_SLOT_NAMES.get(diff["slot"], f"Slot {diff['slot']}")
    return f"{name} {diff['field']}: plan {diff['expected']}, inverter {diff['actual']}"

TARIFF_BAND_LABELS = {"offpeak": "Off-peak", "day": "Day", "peak": "Peak", "export_peak": "Peak export"}

def _pence(value: float | None) -> str:
    return "?" if value is None else f"{round(value, 1)}p"

def _watchdog_rate_kw(plan: dict[str, Any]) -> float | None:
    """Rate the charge watchdog expects: the gentle rate on a live gentle night (phase 23)."""
    if plan.get("gentle_charge_applied") and plan.get("gentle_charge_rate_kw"):
        return plan["gentle_charge_rate_kw"]
    return plan.get("used_charge_rate_kw")

def _pct(value: float | None) -> str:
    return "?" if value is None else f"{round(value)}%"

def _kwh(value: float | None) -> str:
    return "? kWh" if value is None else f"{value} kWh"

def _reserve_note(trim_target: int, reserve: int) -> str:
    return " to keep the evening reserve" if trim_target == reserve else ""
