# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Battery capacity estimate, state of health, low-SOC floor."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .core import (
    CHARGE_EFFICIENCY,
    _is_number,
    _median,
    _snap,
    _snap_delta,
)


# Battery capacity tracking (phase 34). Shadow: never changes battery_capacity.
CAPACITY_MIN_SWING_SOC = 30.0

CAPACITY_MIN_SAMPLES = 5

CAPACITY_ALERT_FRACTION = 0.9

CAPACITY_DISCHARGE_MONTHS = (10, 11, 12, 1, 2, 3)  # no solar after 19:00


def capacity_samples(
    snapshots: dict[str, dict[str, Any]], month: int, efficiency: float = CHARGE_EFFICIENCY
) -> list[dict[str, Any]]:
    """Usable kWh per 100% SOC from one day's meter snapshots: up to one charge and one discharge sample.

    Charge 02:00→05:00: (grid in − load − export) × efficiency over SOC gained.
    Discharge 19:00→22:00 (Oct–Mar only, so no solar is mixed in):
    (load − grid in + export) / efficiency over SOC lost. Swings under
    ``CAPACITY_MIN_SWING_SOC`` or any missing meter give no sample.
    """
    out = []
    windows = [("02:00", "05:00", "charge")]
    if month in CAPACITY_DISCHARGE_MONTHS:
        windows.append(("19:00", "22:00", "discharge"))
    for start, end, kind in windows:
        imp = _snap_delta(snapshots, start, end, "grid_import_kwh")
        exp = _snap_delta(snapshots, start, end, "grid_export_kwh")
        load = _snap_delta(snapshots, start, end, "load_kwh")
        soc_a, soc_b = _snap(snapshots, start, "soc"), _snap(snapshots, end, "soc")
        if None in (imp, exp, load, soc_a, soc_b):
            continue
        swing = soc_b - soc_a if kind == "charge" else soc_a - soc_b
        if swing < CAPACITY_MIN_SWING_SOC:
            continue
        energy = (imp - load - exp) * efficiency if kind == "charge" else (load - imp + exp) / efficiency
        if energy <= 0:
            continue
        out.append({"kind": kind, "kwh_per_100": round(energy / swing * 100, 2), "swing_soc": round(swing, 1)})
    return out


def capacity_estimate(samples: list[dict[str, Any]], min_samples: int = CAPACITY_MIN_SAMPLES) -> float | None:
    """Median kWh per 100% SOC over ``samples``; None with fewer than ``min_samples``."""
    values = sorted(
        float(s["kwh_per_100"]) for s in samples if isinstance(s.get("kwh_per_100"), (int, float)) and s["kwh_per_100"] > 0
    )
    if len(values) < min_samples:
        return None
    return round(_median(values), 2)


def capacity_low(estimate: float | None, configured_kwh: float, fraction: float = CAPACITY_ALERT_FRACTION) -> bool:
    """Measured capacity below ``fraction`` of the configured figure; False when unknown."""
    return estimate is not None and configured_kwh > 0 and estimate < configured_kwh * fraction


def capacity_estimate_pending(record: dict[str, Any] | None) -> bool:
    """No usable capacity estimate stored yet, so re-measure before the monthly run."""
    return not record or record.get("estimate_kwh") is None


FLOOR_HIT_MARGIN = 1.0  # evening SOC within this of the floor counts as a day that sat on it

_FLOOR_KEY_WORDS = ("cap", "soc", "low", "shutdown", "restart")


def battery_floor_soc(settings: Any) -> tuple[float | None, str | None, list[str]]:
    """(floor %, key it came from, candidate key names) from the settings read-back.

    The key is the first whose lower-cased name contains both "low" and "cap" with a 0-100 value.
    The candidate names (keys only, never values) are returned so the log shows what the cloud
    exposes when no floor is found. Not a dict -> (None, None, []).
    """
    if not isinstance(settings, dict):
        return None, None, []
    names = sorted(str(k) for k in settings if any(w in str(k).lower() for w in _FLOOR_KEY_WORDS))
    for name in names:
        lowered = name.lower()
        if "low" in lowered and "cap" in lowered:
            try:
                value = float(settings[name])
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 100:
                return value, name, names
    return None, None, names


def floor_saving_gbp(
    days: list[dict[str, Any]],
    floor_soc: float | None,
    candidate_soc: float,
    capacity_kwh: float | None,
    prices_pence: dict[str, float | None],
) -> dict[str, Any]:
    """£ a lower inverter floor would have saved over ``days`` (paired days: evening_soc, away).

    A day counts when its evening SOC sat on the floor (within FLOOR_HIT_MARGIN); each such day
    would have used (floor - candidate)% more of the battery instead of buying that energy at
    the day rate, the replacement costing off-peak / CHARGE_EFFICIENCY. Away days are skipped
    (rule 18). Missing floor, capacity or prices -> saving None (rule 11).
    """
    usable = [d for d in days if not d.get("away") and _is_number(d.get("evening_soc"))]
    hits = [d for d in usable if floor_soc is not None and d["evening_soc"] <= floor_soc + FLOOR_HIT_MARGIN]
    day, offpeak = prices_pence.get("day"), prices_pence.get("offpeak")
    saving = None
    if floor_soc is not None and _is_number(capacity_kwh) and capacity_kwh > 0 and day is not None and offpeak is not None:
        per_day_kwh = max(0.0, floor_soc - candidate_soc) / 100 * capacity_kwh
        saving = round(len(hits) * per_day_kwh * (day - offpeak / CHARGE_EFFICIENCY) / 100, 2)
    return {"days": len(usable), "hit_days": len(hits), "saving_gbp": saving}


# ---- Phase 75: battery state of health from the cloud register snapshot ----------------------------
# Source: GET /api/v1/workdata/dynamic?...&type=1 (portal "Operation data", captured 04/11/2026). Each
# record is {"<Name>(<unit>)/<register>": "<value>"}; names are matched without the unit/register suffix
# so a register renumbering does not break the parse.

NOMINAL_PACK_VOLTAGE = 51.2

_FAULT_WORD_NAMES = (
    "WarningMessageWord1", "WarningMessageWord2",
    "FaultInformationWord1", "FaultInformationWord2", "FaultInformationWord3", "FaultInformationWord4",
    "DeyeBatBatteryFault1", "DeyeBatBatteryFault2", "DeyeBatBatteryAlarm1", "DeyeBatBatteryAlarm2",
)

_PACK_FAULT_SUFFIXES = ("Fault1", "Fault2", "Warming1", "Warming2")

_MAX_PACKS = 32


def _workdata_index(record: dict) -> dict[str, Any]:
    index = {}
    for key, value in record.items():
        name = str(key).split("(")[0].split("/")[0].strip()
        index.setdefault(name, value)
    return index


def _workdata_float(index: dict, name: str) -> float | None:
    value = index.get(name)
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nonzero_word(value) -> bool:
    if value in (None, "", "--"):
        return False
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return True  # unknown non-numeric text in a fault word counts as a fault


def parse_battery_health(record) -> dict | None:
    """Battery health and fault words from one register snapshot; None when it carries neither SOH nor a pack.

    Packs with a blank serial are empty slots. Every missing value stays None (rule 11). Cell spread is
    max-min cell voltage in mV across all packs; health_kwh is sum(pack Ah x pack SOH) at 51.2 V."""
    if not isinstance(record, dict):
        return None
    index = _workdata_index(record)
    packs = []
    for i in range(1, _MAX_PACKS + 1):
        sn = index.get(f"Pack{i}Sn")
        if sn in (None, "", "--"):
            continue
        cell_max = _workdata_float(index, f"Pack{i}MaxCell")
        cell_min = _workdata_float(index, f"Pack{i}MinCell")
        packs.append({
            "pack": i,
            "soh_pct": _workdata_float(index, f"Pack{i}Soh"),
            "cycles": _workdata_float(index, f"Pack{i}CycleNumber"),
            "total_ah": _workdata_float(index, f"Pack{i}TotalCapacity"),
            "soc_pct": _workdata_float(index, f"Pack{i}Soc"),
            "cell_max_v": cell_max,
            "cell_min_v": cell_min,
            "cell_spread_mv": round((cell_max - cell_min) * 1000, 1)
            if cell_max is not None and cell_min is not None else None,
            "temp_c": _workdata_float(index, f"Pack{i}TemperateAve"),
            "faults": {
                f"Pack{i}{suffix}": index.get(f"Pack{i}{suffix}")
                for suffix in _PACK_FAULT_SUFFIXES if _nonzero_word(index.get(f"Pack{i}{suffix}"))
            },
        })
    soh = _workdata_float(index, "DeyeBatBatterySoh")
    if soh is None and not packs:
        return None
    faults = {name: index.get(name) for name in _FAULT_WORD_NAMES if _nonzero_word(index.get(name))}
    for pack in packs:
        faults.update(pack.pop("faults"))

    def _known(key):
        return [p[key] for p in packs if p[key] is not None]

    pack_ah = [p["total_ah"] for p in packs]
    nameplate_ah = sum(pack_ah) if pack_ah and None not in pack_ah else None
    health_ah = (
        sum(p["total_ah"] * p["soh_pct"] / 100 for p in packs)
        if packs and all(p["total_ah"] is not None and p["soh_pct"] is not None for p in packs) else None
    )
    cells_max, cells_min = _known("cell_max_v"), _known("cell_min_v")
    return {
        "sample_time": index.get("Time"),
        "soh_pct": soh if soh is not None else (min(_known("soh_pct")) if _known("soh_pct") else None),
        "pack_count": len(packs),
        "cycles_max": max(_known("cycles")) if _known("cycles") else None,
        "nameplate_kwh": round(nameplate_ah * NOMINAL_PACK_VOLTAGE / 1000, 2) if nameplate_ah else None,
        "health_kwh": round(health_ah * NOMINAL_PACK_VOLTAGE / 1000, 2) if health_ah else None,
        "cell_spread_mv": round((max(cells_max) - min(cells_min)) * 1000, 1) if cells_max and cells_min else None,
        "cell_temp_c": _workdata_float(index, "DeyeBatBatteryTemp"),
        "bms_charge_limit_a": _workdata_float(index, "DeyeBatChargeLimitCurrent"),
        "bms_discharge_limit_a": _workdata_float(index, "DeyeBatDischargeLimitCurrent"),
        "charge_total_kwh": _workdata_float(index, "BatChargeTotal"),
        "discharge_total_kwh": _workdata_float(index, "BatDischargeTotal"),
        "inverter_temp_c": _workdata_float(index, "DcTemp"),
        "fault_words": faults,
        "packs": packs,
    }


def health_fault_flag(health) -> bool | None:
    """True when any inverter/BMS fault or warning word is non-zero; None without a snapshot."""
    if not isinstance(health, dict):
        return None
    return bool(health.get("fault_words"))


# Thresholds for the charge-stall hints: LFP charge acceptance falls off below ~10 C; a >50 mV cell
# spread near full triggers BMS top-balancing stops; a BMS limit under ~50 A caps a 5 kW charger.
STALL_COLD_CELL_C = 10.0

STALL_CELL_SPREAD_MV = 50.0

STALL_BMS_LIMIT_A = 50.0


def bms_stall_hints(health) -> list[str]:
    """Plain reasons the BMS may have held the charge back, from one snapshot (empty when none apply)."""
    if not isinstance(health, dict):
        return []
    hints = []
    temp = health.get("cell_temp_c")
    if temp is not None and temp < STALL_COLD_CELL_C:
        hints.append(f"cold cells ({temp:.0f} \u00b0C)")
    spread = health.get("cell_spread_mv")
    if spread is not None and spread >= STALL_CELL_SPREAD_MV:
        hints.append(f"cell imbalance ({spread:.0f} mV)")
    limit = health.get("bms_charge_limit_a")
    if limit is not None and limit < STALL_BMS_LIMIT_A:
        hints.append(f"BMS charge limit {limit:.0f} A")
    if health.get("fault_words"):
        hints.append("BMS/inverter fault word set")
    return hints


def bms_summary_line(health) -> str | None:
    """One-line BMS context for an alert, e.g. 'BMS: SOH 97%, cells 12 C, spread 3 mV, limit 128 A.'"""
    if not isinstance(health, dict):
        return None
    parts = []
    if health.get("soh_pct") is not None:
        parts.append(f"SOH {health['soh_pct']:.0f}%")
    if health.get("cell_temp_c") is not None:
        parts.append(f"cells {health['cell_temp_c']:.0f} \u00b0C")
    if health.get("cell_spread_mv") is not None:
        parts.append(f"spread {health['cell_spread_mv']:.0f} mV")
    if health.get("bms_charge_limit_a") is not None:
        parts.append(f"limit {health['bms_charge_limit_a']:.0f} A")
    if not parts:
        return None
    hints = bms_stall_hints(health)
    return "BMS: " + ", ".join(parts) + "." + (f" Possible cause: {'; '.join(hints)}." if hints else "")


HEALTH_TREND_MIN_DAYS = 30


def battery_health_trend(history: list[dict], battery_cost_gbp=None, lifetime_kwh=None) -> dict:
    """SOH fade and measured wear cost from daily ledger points (date, soh_pct, cycles_max, discharge_total_kwh).

    Rates need >= 30 days between the first and last usable points, else None. measured_wear_p_per_kwh =
    cost x SOH lost / kWh discharged; configured_wear_p_per_kwh = cost / lifetime kWh, for comparison.
    Shadow only: nothing feeds the wear term yet."""
    def _parse(point):
        try:
            return datetime.fromisoformat(str(point.get("date"))).date()
        except (TypeError, ValueError):
            return None

    points = sorted(
        (p for p in history or [] if _parse(p) is not None and p.get("soh_pct") is not None), key=_parse
    )
    configured = (
        round(float(battery_cost_gbp) * 100 / float(lifetime_kwh), 2)
        if battery_cost_gbp and lifetime_kwh else None
    )
    out = {
        "points": len(points), "days": None, "soh_change_pct": None, "cycles_added": None,
        "discharged_kwh": None, "fade_pct_per_1000_cycles": None, "fade_pct_per_year": None,
        "measured_wear_p_per_kwh": None, "configured_wear_p_per_kwh": configured,
    }
    if len(points) < 2:
        return out
    first, last = points[0], points[-1]
    days = (_parse(last) - _parse(first)).days
    out["days"] = days
    if days < HEALTH_TREND_MIN_DAYS:
        return out
    soh_lost = float(first["soh_pct"]) - float(last["soh_pct"])
    out["soh_change_pct"] = round(-soh_lost, 2)
    out["fade_pct_per_year"] = round(soh_lost * 365 / days, 2)
    if first.get("cycles_max") is not None and last.get("cycles_max") is not None:
        cycles = float(last["cycles_max"]) - float(first["cycles_max"])
        out["cycles_added"] = cycles
        if cycles > 0:
            out["fade_pct_per_1000_cycles"] = round(soh_lost * 1000 / cycles, 2)
    if first.get("discharge_total_kwh") is not None and last.get("discharge_total_kwh") is not None:
        discharged = float(last["discharge_total_kwh"]) - float(first["discharge_total_kwh"])
        out["discharged_kwh"] = round(discharged, 1)
        if discharged > 0 and battery_cost_gbp:
            out["measured_wear_p_per_kwh"] = round(float(battery_cost_gbp) * 100 * (soh_lost / 100) / discharged, 2)
    return out
