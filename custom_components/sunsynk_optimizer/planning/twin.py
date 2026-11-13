# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Digital twin (phase 72): replay logged days through the real planner and a half-hourly battery model.

Offline only (used by `tools/twin.py`); nothing here is called from the live
integration. Per day the twin takes the 02:00 SOC and the load and solar the
meter snapshots imply (as phase 37 derives them), asks the real
`select_target_soc` for the overnight target, charges to it in the 02:00-05:00
window, then runs the day causally slot by slot: solar covers load, surplus
charges the battery then exports, deficits discharge it down to the floor, the
grid covers the rest. No foresight (unlike `mpc_solve`), so differences between
parameter sets are the planner's own. Missing data → `None`, never 0.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    _BASELINE_SEGMENTS,
    _is_number,
    _snap,
    hhmm_to_minutes,
    price_at,
)
from .overnight import select_target_soc
from .reports import _day_flows

TWIN_SLOT_MINUTES = 30
TWIN_START_MINUTES = 2 * 60
TWIN_END_MINUTES = 22 * 60
TWIN_CHARGE_FROM = 2 * 60
TWIN_CHARGE_TO = 5 * 60
TWIN_SUNRISE_H = 6.5  # only shapes solar within a segment; segment totals come from the meters
TWIN_SUNSET_H = 18.0

TWIN_DEFAULTS: dict[str, float] = {
    "capacity_kwh": 10.0,
    "charge_kw": 3.0,
    "floor_soc": float(BATTERY_LOW_SOC),
    "charge_eff": CHARGE_EFFICIENCY,
    "discharge_eff": DISCHARGE_EFFICIENCY,
    "target_offset": 0.0,  # percentage points added to a target below 100
    "load_scale": 1.0,  # × avg_consumption_kw handed to the planner
}


def _bell(hour: float) -> float:
    frac = (hour - TWIN_SUNRISE_H) / (TWIN_SUNSET_H - TWIN_SUNRISE_H)
    return math.sin(math.pi * frac) if 0.0 < frac < 1.0 else 0.0


def twin_slots(snapshots: dict[str, dict[str, Any]], capacity_kwh: float) -> list[dict[str, Any]] | None:
    """Half-hourly 02:00-22:00 {minute, band, load_kwh, solar_kwh}; load even per segment, solar bell-shaped."""
    flows = _day_flows(snapshots, capacity_kwh)
    if flows is None:
        return None
    slots: list[dict[str, Any]] = []
    for (start, band, load, solar), (_s, end, _b, _sunny) in zip(flows[1:], _BASELINE_SEGMENTS[1:]):
        first, last = hhmm_to_minutes(start), hhmm_to_minutes(end)
        minutes = list(range(first, last, TWIN_SLOT_MINUTES))
        weights = [_bell((m + TWIN_SLOT_MINUTES / 2) / 60) for m in minutes]
        total = sum(weights)
        for m, w in zip(minutes, weights):
            share = w / total if total > 0 else 1 / len(minutes)
            slots.append({
                "minute": m, "band": band,
                "load_kwh": load / len(minutes), "solar_kwh": solar * share,
            })
    return slots


def twin_target(plan: dict[str, Any], params: dict[str, float]) -> int | None:
    """The overnight target the real planner picks from a logged import_plan, with the twin's knobs applied."""
    forecasts = [plan.get(k) for k in ("solar_forecast_kwh", "raw_forecast_kwh")]
    forecasts = [float(f) for f in forecasts if _is_number(f)]
    load = plan.get("avg_consumption_kw")
    if not forecasts or not _is_number(load):
        return None
    hours = plan.get("hours_to_solar")
    decision = select_target_soc(
        is_full_day=bool(plan.get("full_charge_due")) and not plan.get("full_charge_skip_would"),
        low_solar_forecast_kwh=min(forecasts),
        band=plan.get("forecast_band") or "",
        hours_to_solar=float(hours) if _is_number(hours) else None,
        avg_consumption_kw=float(load) * params["load_scale"],
        battery_capacity_kwh=params["capacity_kwh"],
    )
    target = decision.target_soc
    if target < 100:
        target += params["target_offset"]
    return int(round(max(0.0, min(100.0, target))))


def twin_run(
    slots: list[dict[str, Any]],
    soc_start: float,
    target_soc: float,
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    params: dict[str, float],
) -> dict[str, Any] | None:
    """Causal half-hourly run 02:00-22:00. None when a band price or the start SOC is missing."""
    if not slots or not _is_number(soc_start):
        return None
    cap, floor = params["capacity_kwh"], params["floor_soc"]
    ce, de = params["charge_eff"], params["discharge_eff"]
    if cap <= 0:
        return None
    energy = max(0.0, min(100.0, float(soc_start))) / 100 * cap
    floor_kwh, step = floor / 100 * cap, TWIN_SLOT_MINUTES / 60
    cost = import_kwh = export_kwh = 0.0
    soc_06: float | None = None
    for slot in slots:
        import_p = prices_pence.get(slot["band"])
        if import_p is None:
            return None
        export_p = price_at(export_bands, slot["minute"]) or 0.0
        net = slot["load_kwh"] - slot["solar_kwh"]  # + deficit, − surplus
        if TWIN_CHARGE_FROM <= slot["minute"] < TWIN_CHARGE_TO:
            want = max(0.0, target_soc / 100 * cap - energy)
            stored = min(want, params["charge_kw"] * step * ce)
            energy += stored
            net += stored / ce
        elif net < 0:
            stored = min(-net * ce, cap - energy)
            energy += stored
            net += stored / ce
        elif net > 0:
            drawn = min(net / de, max(0.0, energy - floor_kwh))
            energy -= drawn
            net -= drawn * de
        if net > 0:
            import_kwh += net
            cost += net * import_p
        else:
            export_kwh -= net
            cost += net * export_p
        if soc_06 is None and slot["minute"] + TWIN_SLOT_MINUTES >= 6 * 60:
            soc_06 = energy / cap * 100
    soc_end = energy / cap * 100
    offpeak = prices_pence.get("offpeak")
    terminal = 0.0 if offpeak is None else max(0.0, energy - floor_kwh) * offpeak / ce
    return {
        "soc_06": None if soc_06 is None else round(soc_06, 1),
        "soc_22": round(soc_end, 1),
        "import_kwh": round(import_kwh, 2),
        "export_kwh": round(export_kwh, 2),
        "cost_gbp": round((cost - terminal) / 100, 3),
    }


def twin_day(
    snapshots: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    params: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """One logged day through planner + battery model; None when any input is missing or the day was away."""
    p = {**TWIN_DEFAULTS, **(params or {})}
    soc = _snap(snapshots, "02:00", "soc")
    slots = twin_slots(snapshots, p["capacity_kwh"])
    target = twin_target(plan, p)
    if plan.get("away") or soc is None or slots is None or target is None:
        return None
    run = twin_run(slots, soc, max(target, soc), prices_pence, export_bands, p)
    return None if run is None else {"target_soc": target, **run}


def twin_replay(
    days: dict[str, dict[str, Any]],
    plans: dict[str, dict[str, Any]],
    mornings: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    params: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Replay every day that has snapshots and a plan: totals, per-day rows and the model's SOC error vs the logs.

    `days` is {date: {HH:MM: meter_snapshot}}; `mornings` supplies the real 06:00 SOC for calibration.
    """
    rows: list[dict[str, Any]] = []
    err_06: list[float] = []
    err_22: list[float] = []
    skipped = 0
    for date in sorted(days):
        plan = plans.get(date)
        out = twin_day(days[date], plan, prices_pence, export_bands, params) if plan else None
        if out is None:
            skipped += 1
            continue
        real_06 = (mornings.get(date) or {}).get("morning_soc")
        real_22 = _snap(days[date], "22:00", "soc")
        if _is_number(real_06) and out["soc_06"] is not None:
            err_06.append(abs(out["soc_06"] - real_06))
        if real_22 is not None:
            err_22.append(abs(out["soc_22"] - real_22))
        rows.append({"date": date, **out})

    def _mae(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 1) if values else None

    return {
        "days": len(rows),
        "skipped": skipped,
        "cost_gbp": round(sum(r["cost_gbp"] for r in rows), 2) if rows else None,
        "import_kwh": round(sum(r["import_kwh"] for r in rows), 1),
        "export_kwh": round(sum(r["export_kwh"] for r in rows), 1),
        "soc_06_mae": _mae(err_06),
        "soc_22_mae": _mae(err_22),
        "rows": rows,
    }


def twin_sweep(
    days: dict[str, dict[str, Any]],
    plans: dict[str, dict[str, Any]],
    mornings: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    grid: dict[str, list[float]],
    base: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Every combination of `grid` values replayed, cheapest first; each row carries its delta vs the base run."""
    unknown = sorted(set(grid) - set(TWIN_DEFAULTS))
    if unknown:
        raise ValueError(f"unknown twin parameter(s): {', '.join(unknown)}")
    base_run = twin_replay(days, plans, mornings, prices_pence, export_bands, base)
    names = sorted(grid)
    results = []
    for values in itertools.product(*(grid[n] for n in names)):
        params = {**(base or {}), **dict(zip(names, values))}
        run = twin_replay(days, plans, mornings, prices_pence, export_bands, params)
        delta = None
        if run["cost_gbp"] is not None and base_run["cost_gbp"] is not None and run["days"] == base_run["days"]:
            delta = round(run["cost_gbp"] - base_run["cost_gbp"], 2)
        results.append({
            "params": dict(zip(names, values)), "days": run["days"], "cost_gbp": run["cost_gbp"],
            "delta_gbp": delta, "import_kwh": run["import_kwh"], "soc_06_mae": run["soc_06_mae"],
        })
    results.sort(key=lambda r: (r["cost_gbp"] is None, r["cost_gbp"] if r["cost_gbp"] is not None else 0.0))
    return results
