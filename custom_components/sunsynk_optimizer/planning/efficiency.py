# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Measured round-trip efficiency (phase 103, shadow): BMS lifetime totals, SOC-corrected. Pure.

The BMS charge and discharge totals count energy at the battery terminals, so the measured figure
excludes the inverter. The AC round trip the plan uses is that figure times the assumed inverter
legs (CHARGE_EFFICIENCY x DISCHARGE_EFFICIENCY); the inverter part is assumed, not measured.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .core import CHARGE_EFFICIENCY, DISCHARGE_EFFICIENCY, ROUND_TRIP_EFFICIENCY, _num, round_or_none
from .overnight import OUTCOME_COLD_C

RT_MIN_CHARGE_KWH = 20.0        # total charged over the window before a figure is reported
RT_MIN_GROUP_CHARGE_KWH = 10.0  # ...and per warm / cold group
RT_PLAUSIBLE = (0.5, 1.05)      # a battery-terminal figure outside this is a unit or counter problem, not an efficiency


def round_trip_intervals(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charge, discharge and SOC change between consecutive-day `battery_health` records.

    A record needs both lifetime totals and the SOC read with it (`soc`). A gap day, a missing field or a
    counter that ran backwards drops the interval. `temp_c` is the mean of the cell temperatures known.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.get("type") == "battery_health" and isinstance(r.get("date"), str):
            by_date[r["date"]] = r
    out: list[dict[str, Any]] = []
    for d in sorted(by_date):
        try:
            prev = by_date.get((date.fromisoformat(d) - timedelta(days=1)).isoformat())
        except ValueError:
            continue
        if prev is None:
            continue
        cur = by_date[d]
        c1, c0 = _num(cur.get("charge_total_kwh")), _num(prev.get("charge_total_kwh"))
        d1, d0 = _num(cur.get("discharge_total_kwh")), _num(prev.get("discharge_total_kwh"))
        s1, s0 = _num(cur.get("soc")), _num(prev.get("soc"))
        if None in (c1, c0, d1, d0, s1, s0) or c1 < c0 or d1 < d0:
            continue
        temps = [t for t in (_num(cur.get("cell_temp_c")), _num(prev.get("cell_temp_c"))) if t is not None]
        out.append({
            "date": d, "charge_kwh": c1 - c0, "discharge_kwh": d1 - d0, "delta_soc": s1 - s0,
            "temp_c": sum(temps) / len(temps) if temps else None,
        })
    return out


def _group(intervals: list[dict[str, Any]], capacity_kwh: float, min_charge: float) -> dict[str, Any] | None:
    charged = sum(i["charge_kwh"] for i in intervals)
    if not intervals or charged < min_charge or capacity_kwh <= 0:
        return None
    discharged = sum(i["discharge_kwh"] for i in intervals)
    stored = sum(i["delta_soc"] for i in intervals) / 100 * capacity_kwh  # energy still in the battery, not yet discharged
    eff = (discharged + stored) / charged
    if not RT_PLAUSIBLE[0] <= eff <= RT_PLAUSIBLE[1]:
        return None
    return {
        "days": len(intervals),
        "charged_kwh": round(charged, 1),
        "discharged_kwh": round(discharged, 1),
        "stored_kwh": round(stored, 2),
        "battery_eff": round(eff, 3),
    }


def ac_round_trip(battery_eff: float | None) -> float | None:
    """Battery-terminal efficiency (capped at 1) times the assumed inverter legs; None when unknown."""
    if battery_eff is None:
        return None
    return round(min(1.0, battery_eff) * CHARGE_EFFICIENCY * DISCHARGE_EFFICIENCY, 3)


def round_trip_measured(
    intervals: list[dict[str, Any]], capacity_kwh: float, cold_c: float = OUTCOME_COLD_C
) -> dict[str, Any]:
    """Energy-weighted round trip over the intervals, with warm and cold groups (cell temperature below `cold_c`).

    `overall` is None until RT_MIN_CHARGE_KWH has been charged; a group is None below RT_MIN_GROUP_CHARGE_KWH
    (an interval without a temperature is in `overall` only). ac_eff is the overall figure on the AC side.
    """
    overall = _group(intervals, capacity_kwh, RT_MIN_CHARGE_KWH)
    cold = _group([i for i in intervals if i["temp_c"] is not None and i["temp_c"] < cold_c], capacity_kwh, RT_MIN_GROUP_CHARGE_KWH)
    warm = _group([i for i in intervals if i["temp_c"] is not None and i["temp_c"] >= cold_c], capacity_kwh, RT_MIN_GROUP_CHARGE_KWH)
    return {
        "type": "round_trip_measured",
        "intervals": len(intervals),
        "overall": overall,
        "warm": warm,
        "cold": cold,
        "battery_eff": overall["battery_eff"] if overall else None,
        "ac_eff": ac_round_trip(overall["battery_eff"] if overall else None),
        "assumed_inverter_eff": round(CHARGE_EFFICIENCY * DISCHARGE_EFFICIENCY, 3),
    }


def round_trip_shadow(
    kwh: float | None, buy_pence: float | None, sell_pence: float | None, measured_ac_eff: float | None,
    wear_pence: float | None = None,
) -> dict[str, Any] | None:
    """Gain of storing `kwh` bought at `buy_pence` and used or sold at `sell_pence`, under 0.85 and the measured figure.

    None when the measured figure, a price or the energy is missing (never 0). worth_* is gain > 0.
    """
    if measured_ac_eff is None or measured_ac_eff <= 0 or None in (kwh, buy_pence, sell_pence) or not kwh or kwh <= 0:
        return None

    def gain(eff: float) -> float:
        return kwh * (sell_pence - buy_pence / eff - (wear_pence or 0.0)) / 100

    default, measured = gain(ROUND_TRIP_EFFICIENCY), gain(measured_ac_eff)
    return {
        "measured_eff": measured_ac_eff,
        "gain_gbp_default": round_or_none(default),
        "gain_gbp_measured": round_or_none(measured),
        "worth_default": default > 0,
        "worth_measured": measured > 0,
        "breakeven_eff": round_trip_breakeven(buy_pence, sell_pence),
    }


def round_trip_breakeven(buy_pence: float | None, sell_pence: float | None) -> float | None:
    """AC round trip below which storing stops paying (buy / sell); None without both prices."""
    if buy_pence is None or sell_pence is None or sell_pence <= 0:
        return None
    return round(buy_pence / sell_pence, 3)
