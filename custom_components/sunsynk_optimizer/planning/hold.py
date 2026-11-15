# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Daytime hold floor on the inverter's own timer slot (phase 114). HA-free, pure.

A peak-cover "hold" keeps the battery at the SOC the 16:00-19:00 peak needs. Instead of a Flux 1
slot, the SOC floor (``capN``) of the timer slot that covers now -> 16:00 is raised to that SOC and
put back at 16:00."""

from __future__ import annotations

from typing import Any

from .core import _as_float, _is_number, hhmm_to_minutes

HOLD_END_MINUTES = 16 * 60


def daytime_hold_slot(slots: Any, now_minute: int, end_minute: int = HOLD_END_MINUTES) -> int | None:
    """1-based number of the timer slot that covers now -> ``end_minute`` without a slot change, else None.

    ``slots`` is the inverter's slot list in order (``tou_slots_from_settings``); each slot runs from its
    start to the next slot's start, the last one to the first one's start. A slot that ends before the
    hold end, or a list that is not six readable slots, gives None."""
    if not isinstance(slots, list) or len(slots) < 2:
        return None
    starts = []
    for slot in slots:
        minute = hhmm_to_minutes(str((slot or {}).get("time") or "")) if isinstance(slot, dict) else None
        if minute is None:
            return None
        starts.append(minute)
    for i, start in enumerate(starts):
        nxt = starts[(i + 1) % len(starts)]
        span = (nxt - start) % 1440 or 1440
        if (now_minute - start) % 1440 < span and (now_minute - start) % 1440 + (end_minute - now_minute) <= span:
            return i + 1
    return None


def daytime_hold_floor(decision: Any, required_soc: Any, current_cap: Any, soc: Any) -> int | None:
    """The SOC to write to the slot floor, or None when nothing should be written.

    Only a "hold" decision qualifies. The floor must rise (a floor already at or above the requirement is
    left alone) and must not exceed the SOC the battery has now, so the hold never forces a grid charge.
    Missing input is None, never 0."""
    if decision != "hold" or not _is_number(required_soc) or not _is_number(soc):
        return None
    hold = min(100, int(round(float(required_soc))))
    current = _as_float(current_cap)
    if current is None or hold <= current or hold > float(soc):
        return None
    return hold


def hold_readback_ok(settings: Any, slot: int, expected: Any) -> bool | None:
    """True when ``cap<slot>`` reads back as ``expected``; None when it can't be read."""
    if not isinstance(settings, dict) or not _is_number(expected):
        return None
    value = _as_float(settings.get(f"cap{slot}"))
    return None if value is None else int(round(value)) == int(round(float(expected)))
