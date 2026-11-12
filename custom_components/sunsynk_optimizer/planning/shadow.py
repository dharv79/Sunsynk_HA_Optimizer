# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Shadow-mode planners: MPC, dynamic tariff, native TOU."""

from __future__ import annotations

from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    HALF_HOURS_PER_DAY,
    MPC_DAY_FROM_MINUTES,
    _BASELINE_SEGMENTS,
    _as_float,
    _is_number,
    _seg_value,
    _snap,
    band_key_at,
    hhmm_to_minutes,
    minutes_to_hhmm,
    price_at,
)
from .load import (
    mpc_load_kw,
)
from .reports import (
    _day_flows,
)


# Receding-horizon MPC planner (phase 69). Shadow only: logged next to the rule-based plan, never pushed.
MPC_SLOT_MINUTES = 30

MPC_MAX_SLOTS = 48  # 24 h ahead


def mpc_slots(
    start_minute: int,
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    hourly_today: dict[int, float] | None,
    hourly_tomorrow: dict[int, float] | None,
    loads: dict[str, float],
    load_profile: list[float] | None = None,
    import_vectors: dict[int, list[float]] | None = None,
    export_flat_p: float | None = None,
) -> list[dict[str, Any]] | None:
    """Half-hour slots from `start_minute` (rounded down) for up to 24 h.

    Solar per slot = half the forecast hour's kWh. Without tomorrow's forecast
    the horizon stops at the next 06:00 (before tomorrow's solar matters).
    `load_profile` (48 kWh values by slot of day, phase 71) overrides `loads`.
    A slot with no import price → None (no plan); no export price exports for 0p.
    `import_vectors` ({day offset: 48 p/kWh}, phase 76) prices import per slot
    instead of the Flux bands; the horizon stops where the vectors end.
    `export_flat_p` overrides the export bands with one rate.
    """
    start = (start_minute // MPC_SLOT_MINUTES) * MPC_SLOT_MINUTES
    slots = []
    for i in range(MPC_MAX_SLOTS):
        absolute = start + i * MPC_SLOT_MINUTES
        day, minute = divmod(absolute, 1440)
        hourly = hourly_today if day == 0 else hourly_tomorrow
        if day == 1 and hourly_tomorrow is None and minute >= MPC_DAY_FROM_MINUTES:
            break
        if import_vectors is not None:
            vector = import_vectors.get(day)
            if not vector or len(vector) != HALF_HOURS_PER_DAY:
                break
            import_p = vector[minute // MPC_SLOT_MINUTES]
        else:
            import_p = prices_pence.get(band_key_at(minute))
        if import_p is None:
            return None
        export_p = export_flat_p if export_flat_p is not None else price_at(export_bands, minute)
        if load_profile is not None and len(load_profile) == 48:
            load = float(load_profile[minute // MPC_SLOT_MINUTES])
        else:
            load = mpc_load_kw(minute, loads) * MPC_SLOT_MINUTES / 60
        solar = float((hourly or {}).get(minute // 60, 0.0) or 0.0) / 2
        slots.append({"minute": minute, "day": day, "import_p": import_p,
                      "export_p": 0.0 if export_p is None else export_p, "load_kwh": load, "solar_kwh": solar})
    return slots or None


def _mpc_step(slot: dict[str, Any], energy: float, wear: float) -> tuple[float, float]:
    """(pence, grid kWh) for one slot moving `energy` kWh into (+) or out of (−) the battery."""
    net = slot["load_kwh"] - slot["solar_kwh"]
    grid = net + (energy / CHARGE_EFFICIENCY if energy > 0 else energy * DISCHARGE_EFFICIENCY)
    pence = grid * (slot["import_p"] if grid > 0 else slot["export_p"])
    if energy > 0:
        pence += energy / CHARGE_EFFICIENCY * wear
    return pence, grid


def mpc_solve(
    slots: list[dict[str, Any]],
    soc_pct: float,
    capacity_kwh: float,
    charge_kw: float,
    discharge_kw: float | None = None,
    floor_soc: int = BATTERY_LOW_SOC,
    wear_pence: float | None = None,
    terminal_pence: float | None = None,
) -> dict[str, Any] | None:
    """Cheapest SOC path over `slots` by dynamic programming on whole-% SOC.

    Per slot the battery may move up to the charge/discharge rate; the grid
    covers load − solar ± battery, priced at the slot's import or export
    price, plus wear on charged kWh. SOC left at the end is worth
    `terminal_pence` per kWh above the floor (default: none). Ties keep the
    smaller move, so the plan holds unless moving pays. None without slots,
    SOC or capacity.
    """
    if not slots or not _is_number(soc_pct) or capacity_kwh <= 0:
        return None
    wear, terminal = wear_pence or 0.0, terminal_pence or 0.0
    start = int(round(max(0.0, min(100.0, soc_pct))))
    low = min(floor_soc, start)
    up = max(0, int(charge_kw * MPC_SLOT_MINUTES / 60 * CHARGE_EFFICIENCY / capacity_kwh * 100))
    down = max(0, int((discharge_kw or charge_kw) * MPC_SLOT_MINUTES / 60 / DISCHARGE_EFFICIENCY / capacity_kwh * 100))
    moves = sorted(range(-down, up + 1), key=abs)
    states = range(low, 101)
    kwh_per_pct = capacity_kwh / 100
    value = {s: -max(0, s - floor_soc) * kwh_per_pct * terminal for s in states}
    choice: list[dict[int, int]] = []
    for slot in reversed(slots):
        nxt, pick = {}, {}
        for s in states:
            best = best_move = None
            for m in moves:
                t = s + m
                if t < low or t > 100 or (m < 0 and t < floor_soc):
                    continue
                pence, _ = _mpc_step(slot, m * kwh_per_pct, wear)
                total = pence + value[t]
                if best is None or total < best - 1e-9:
                    best, best_move = total, m
            nxt[s], pick[s] = best, best_move
        value = nxt
        choice.append(pick)
    choice.reverse()
    soc, path, cost = start, [], 0.0
    for slot, pick in zip(slots, choice):
        move = pick[soc]
        pence, grid = _mpc_step(slot, move * kwh_per_pct, wear)
        cost += pence
        net = slot["load_kwh"] - slot["solar_kwh"]
        tol = kwh_per_pct / 2  # one SOC step can overshoot the load by up to 1%
        if move > 0 and grid > max(0.0, net) + tol:
            action = "grid_charge"
        elif move < 0 and grid < -tol:
            action = "export"
        elif move < 0:
            action = "discharge"
        elif move > 0:
            action = "solar_charge"
        else:
            action = "hold"
        soc += move
        path.append({"time": f"{slot['minute'] // 60:02d}:{slot['minute'] % 60:02d}", "day": slot["day"],
                     "soc": soc, "grid_kwh": round(grid, 2), "action": action})
    return {"cost_gbp": round(cost / 100, 2), "start_soc": start, "slots": path}


def mpc_summary(plan: dict[str, Any] | None) -> dict[str, Any]:
    """The rule-comparable parts of an MPC plan: next action, SOC at the next 05:00, kWh by action."""
    if not plan:
        return {}
    slots = plan["slots"]
    soc_0500 = next((s["soc"] for s in slots if s["time"] == "04:30"), None)
    grid_charge = sum(max(0.0, s["grid_kwh"]) for s in slots if s["action"] == "grid_charge")
    offpeak_charge = sum(max(0.0, s["grid_kwh"]) for s in slots
                         if s["action"] == "grid_charge" and "02:00" <= s["time"] < "05:00")
    return {
        "cost_gbp": plan["cost_gbp"],
        "first_action": slots[0]["action"],
        "first_soc": slots[0]["soc"],
        "soc_0500": soc_0500,
        "grid_charge_kwh": round(grid_charge, 2),
        "offpeak_charge_kwh": round(offpeak_charge, 2),
        "day_charge_kwh": round(grid_charge - offpeak_charge, 2),
        "export_kwh": round(-sum(s["grid_kwh"] for s in slots if s["action"] == "export"), 2),
        "horizon_slots": len(slots),
    }


def _segment_end(start: str) -> str:
    """The `_BASELINE_SEGMENTS` end for a segment start."""
    return next(end for s, end, _b, _sunny in _BASELINE_SEGMENTS if s == start)


def mpc_backtest_day(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    capacity_kwh: float,
    charge_kw: float,
) -> dict[str, float | None]:
    """02:00-22:00 of a logged day: metered cost vs the MPC with hindsight on the same load and solar.

    Each segment's load and solar (as phase 37 derives them) are spread evenly
    over its half-hours; both sides credit the 22:00 SOC above the floor at the
    off-peak refill price, so the gap is the planner's headroom (an upper
    bound: the MPC sees the day perfectly). No wear on either side. Negative when the meters
    imply better battery efficiency than the model's. Missing data → all None.
    """
    empty = {"actual_gbp": None, "mpc_gbp": None, "headroom_gbp": None}
    flows = _day_flows(snapshots, capacity_kwh)
    soc_start, soc_end = _snap(snapshots, "02:00", "soc"), _snap(snapshots, "22:00", "soc")
    offpeak = prices_pence.get("offpeak")
    if flows is None or soc_start is None or soc_end is None or offpeak is None:
        return empty
    terminal = offpeak / CHARGE_EFFICIENCY
    slots, actual = [], 0.0
    ends = [hhmm_to_minutes(end) for _s, end, _b, _sunny in _BASELINE_SEGMENTS]
    for (start, band, load, solar), end_min in zip(flows[1:], ends[1:]):
        start_min = hhmm_to_minutes(start)
        n = (end_min - start_min) // MPC_SLOT_MINUTES
        import_p, export_p = prices_pence.get(band), price_at(export_bands, start_min)
        if import_p is None:
            return empty
        for i in range(n):
            slots.append({"minute": start_min + i * MPC_SLOT_MINUTES, "day": 0, "import_p": import_p,
                          "export_p": 0.0 if export_p is None else export_p,
                          "load_kwh": load / n, "solar_kwh": solar / n})
        imp = _seg_value(snapshots, start, _segment_end(start), "grid_import_kwh")
        exp = _seg_value(snapshots, start, _segment_end(start), "grid_export_kwh")
        actual += imp * import_p - exp * (0.0 if export_p is None else export_p)
    actual -= max(0.0, soc_end - BATTERY_LOW_SOC) / 100 * capacity_kwh * terminal
    plan = mpc_solve(slots, soc_start, capacity_kwh, charge_kw, terminal_pence=terminal)
    if plan is None:
        return empty
    mpc = plan["cost_gbp"] * 100 - max(0, plan["slots"][-1]["soc"] - BATTERY_LOW_SOC) / 100 * capacity_kwh * terminal
    return {"actual_gbp": round(actual / 100, 2), "mpc_gbp": round(mpc / 100, 2),
            "headroom_gbp": round((actual - mpc) / 100, 2)}


# Dynamic-tariff native mode (phase 76). Shadow only: the MPC re-run on Agile half-hourly prices
# next to the Flux plan; logged, never pushed. Control stays on Flux.
AGILE_SPIKE_PENCE = 35.0  # a slot at or above this is a price spike worth flagging


def cheapest_window_plan(
    prices: list[float | None] | None,
    need_kwh: float,
    rate_kw: float,
    from_slot: int = 0,
    max_price_p: float | None = None,
) -> dict[str, Any] | None:
    """Cheapest half-hours from `from_slot` to put `need_kwh` into the battery at `rate_kw`.

    `prices` are p/kWh by half-hour (index 0 = 00:00 of the first day; may
    span two days). Slots with no price or above `max_price_p` (the spike
    guard) are never used. Negative-price slots are always taken (paid to
    import), even past the need. Slots are filled cheapest first; the last
    one may be partial. Returns the chosen slots, merged HH:MM windows, kWh,
    cost and the shortfall when not enough slots qualify; None without
    prices or a charge rate.
    """
    if not prices or not _is_number(rate_kw) or rate_kw <= 0:
        return None
    per_slot = rate_kw * MPC_SLOT_MINUTES / 60
    usable = [
        (float(p), i) for i, p in enumerate(prices)
        if i >= from_slot and _is_number(p) and (max_price_p is None or p <= max_price_p)
    ]
    usable.sort()
    need = max(0.0, float(need_kwh or 0.0))
    chosen: dict[int, float] = {}
    got = cost = 0.0
    for price, i in usable:
        if got >= need - 1e-9 and price >= 0:
            break
        kwh = per_slot if price < 0 else min(per_slot, need - got)
        chosen[i] = kwh
        got += kwh
        cost += kwh * price
    idx = sorted(chosen)
    windows: list[list[str]] = []
    for i in idx:
        start, end = minutes_to_hhmm((i * MPC_SLOT_MINUTES) % 1440), minutes_to_hhmm(((i + 1) * MPC_SLOT_MINUTES) % 1440)
        if windows and windows[-1][1] == start and i - 1 in chosen:
            windows[-1][1] = end
        else:
            windows.append([start, end])
    return {
        "slots": idx,
        "windows": windows,
        "kwh": round(got, 2),
        "cost_p": round(cost, 1),
        "avg_p": round(cost / got, 2) if got > 0 else None,
        "short_kwh": round(max(0.0, need - got), 2),
        "plunge_slots": sum(1 for i in idx if prices[i] < 0),
    }


def dynamic_tariff_summary(
    flux_plan: dict[str, Any] | None,
    agile_plan: dict[str, Any] | None,
    agile_prices: list[float] | None,
    windows: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shadow comparison of the same MPC horizon priced on Flux bands vs Agile half-hours.

    `saving_gbp` is Flux cost − Agile cost (positive = Agile cheaper);
    None when either plan is missing. Standing charges are left out (equal
    in the default rates). Price stats cover the Agile slots in the horizon.
    """
    flux, agile = mpc_summary(flux_plan), mpc_summary(agile_plan)
    priced = [float(p) for p in agile_prices or [] if _is_number(p)]
    saving = None
    if flux.get("cost_gbp") is not None and agile.get("cost_gbp") is not None:
        saving = round(flux["cost_gbp"] - agile["cost_gbp"], 2)
    return {
        "flux_cost_gbp": flux.get("cost_gbp"),
        "agile_cost_gbp": agile.get("cost_gbp"),
        "saving_gbp": saving,
        "agile_grid_charge_kwh": agile.get("grid_charge_kwh"),
        "flux_grid_charge_kwh": flux.get("grid_charge_kwh"),
        "agile_export_kwh": agile.get("export_kwh"),
        "agile_soc_0500": agile.get("soc_0500"),
        "horizon_slots": agile.get("horizon_slots"),
        "min_p": round(min(priced), 2) if priced else None,
        "max_p": round(max(priced), 2) if priced else None,
        "spike_slots": sum(1 for p in priced if p >= AGILE_SPIKE_PENCE),
        "negative_slots": sum(1 for p in priced if p < 0),
        "cheapest_windows": (windows or {}).get("windows"),
        "cheapest_avg_p": (windows or {}).get("avg_p"),
    }


# Native inverter time-of-use slots (phase 73). Shadow only: the six system-mode slots the plan
# would need are logged next to what the inverter holds; nothing is written.
TOU_SLOT_COUNT = 6

TOU_PEAK_FROM_MINUTES = 16 * 60

TOU_PEAK_TO_MINUTES = 19 * 60

TOU_SLOT_STEP_MINUTES = 30


def _tou_setting_keys(n: int) -> tuple[str, str, str]:
    """(start time, SOC, grid-charge flag) setting names for slot n (1-6) in the Sunsynk settings dict."""
    return f"sellTime{n}", f"cap{n}", f"time{n}on"


def _tou_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in ("true", "1", "on"):
        return True
    if text in ("false", "0", "off"):
        return False
    return None


def tou_slots_from_plan(
    charge_start: str | None,
    charge_end: str | None,
    target_soc: float | None,
    floor_soc: int,
    evening_reserve_soc: int | None,
    peak_hold: tuple[str, float] | None = None,
) -> list[dict[str, Any]] | None:
    """The plan as the inverter's six (start, SOC, grid charge) slots.

    Each slot runs from its start to the next one's. In a slot the battery
    discharges no lower than its SOC, and with grid charge on it charges
    from the grid up to that SOC. Mapping: midnight → the floor (the battery
    runs the house to the charge); the charge window → target SOC with grid
    charge on; after it → the floor (the day uses the battery); an optional
    peak hold (start, SOC) from phase 59; 16:00 → the evening reserve (the
    SOC to hold at 19:00); 19:00 → the floor (the reserve then carries the
    house to 02:00). Neighbouring slots with the same
    settings merge, then the longest slot is split on the half-hour until
    there are exactly six (the inverter always has six). None when the
    charge window or target is missing.
    """
    start, end = hhmm_to_minutes(charge_start or ""), hhmm_to_minutes(charge_end or "")
    if start is None or end is None or not _is_number(target_soc) or end <= start:
        return None
    reserve = int(evening_reserve_soc) if _is_number(evening_reserve_soc) else floor_soc
    points: dict[int, tuple[int, bool]] = {
        0: (floor_soc, False),
        start: (int(round(target_soc)), True),
        end: (floor_soc, False),
        TOU_PEAK_FROM_MINUTES: (reserve, False),
        TOU_PEAK_TO_MINUTES: (floor_soc, False),
    }
    if peak_hold is not None:
        hold_at = hhmm_to_minutes(peak_hold[0] or "")
        if hold_at is not None and end < hold_at < TOU_PEAK_FROM_MINUTES and _is_number(peak_hold[1]):
            points[hold_at] = (int(round(peak_hold[1])), False)
    merged: list[list[Any]] = []
    for minute in sorted(points):
        if merged and tuple(merged[-1][1:]) == points[minute]:
            continue
        merged.append([minute, *points[minute]])
    while len(merged) < TOU_SLOT_COUNT:
        lengths = [((merged[i + 1][0] if i + 1 < len(merged) else 1440) - m[0], i) for i, m in enumerate(merged)]
        length, i = max(lengths)
        if length < 2 * TOU_SLOT_STEP_MINUTES:
            break
        mid = merged[i][0] + (length // 2 // TOU_SLOT_STEP_MINUTES) * TOU_SLOT_STEP_MINUTES
        merged.insert(i + 1, [mid, merged[i][1], merged[i][2]])
    if len(merged) > TOU_SLOT_COUNT:
        return None
    return [{"time": minutes_to_hhmm(m), "soc": soc, "grid_charge": grid} for m, soc, grid in merged]


def tou_slots_from_settings(settings: Any) -> list[dict[str, Any]] | None:
    """The six slots the inverter holds, from its settings dict; None if any slot is missing or unreadable."""
    if not isinstance(settings, dict):
        return None
    out = []
    for n in range(1, TOU_SLOT_COUNT + 1):
        time_key, soc_key, grid_key = _tou_setting_keys(n)
        minute = hhmm_to_minutes(str(settings.get(time_key) or ""))
        soc, grid = _as_float(settings.get(soc_key)), _tou_flag(settings.get(grid_key))
        if minute is None or soc is None or grid is None:
            return None
        out.append({"time": minutes_to_hhmm(minute), "soc": int(round(soc)), "grid_charge": grid})
    return out


def tou_diff(current: list[dict[str, Any]] | None, proposed: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Per-slot differences (slot, field, inverter, plan); None when either side is unknown."""
    if current is None or proposed is None:
        return None
    diffs = []
    for n, (cur, new) in enumerate(zip(current, proposed), start=1):
        for key in ("time", "soc", "grid_charge"):
            if cur.get(key) != new.get(key):
                diffs.append({"slot": n, "field": key, "inverter": cur.get(key), "plan": new.get(key)})
    return diffs


def tou_settings_payload(slots: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The settings-API fields for six slots (for a later live mode; unused while in shadow)."""
    if not slots or len(slots) != TOU_SLOT_COUNT:
        return None
    payload: dict[str, Any] = {}
    for n, slot in enumerate(slots, start=1):
        time_key, soc_key, grid_key = _tou_setting_keys(n)
        payload[time_key] = slot["time"]
        payload[soc_key] = str(slot["soc"])
        payload[grid_key] = "true" if slot["grid_charge"] else "false"
    return payload
