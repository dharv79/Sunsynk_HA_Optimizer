# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Evening reserve, peak cover, peak export and trim-defer decisions."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    EVENING_RESERVE_HOURS,
    KPI_RESERVE_SOC,
    PEAK_COVER_LAST_LOG_MINUTES,
    PEAK_COVER_MARGIN,
    PEAK_COVER_PEAK_START_MINUTES,
    PEAK_END_MINUTES,
    PEAK_EXPORT_MIN_KWH,
    PEAK_START_MINUTES,
    ROUND_TRIP_EFFICIENCY,
    TRIM_FROM_MINUTES,
    _is_number,
    round_or_none,
)
from .load import (
    resolve_load_kw,
)


DEFAULT_TRIM_TARGET_SOC = 82


def evening_reserve_soc(
    load_kw: float, battery_capacity_kwh: float,
    hours: float = EVENING_RESERVE_HOURS, floor_soc: int = KPI_RESERVE_SOC,
) -> int:
    """SOC% needed at 19:00 to cover `hours` of load above the floor, capped at 100."""
    return min(100, math.ceil(floor_soc + load_kw * hours / battery_capacity_kwh * 100))


def evening_reserve_choice(
    plan_load_kw: float, learned_kw: float | None, battery_capacity_kwh: float,
    live: bool, hours: float = EVENING_RESERVE_HOURS,
) -> tuple[int, int | None]:
    """(reserve in force, learned-load reserve or None).

    The learned load is clamped to 0.5x-2x the plan rate like phase 12. The
    learned reserve is only in force when ``live``; otherwise it is logged.
    """
    base = evening_reserve_soc(plan_load_kw, battery_capacity_kwh, hours)
    if learned_kw is None:
        return base, None
    load, _src = resolve_load_kw(plan_load_kw, learned_kw)
    learned = evening_reserve_soc(load, battery_capacity_kwh, hours)
    return (learned if live else base), learned


def trim_target_soc(
    soc: float,
    reserve_soc: int | None,
    default: int = DEFAULT_TRIM_TARGET_SOC,
    *,
    minutes: int | None = None,
    plan_target_soc: int | None = None,
    hold_soc: int | None = None,
) -> int | None:
    """Sell-down target floored at the evening reserve; None when that leaves nothing to trim.

    ``hold_soc`` is an active peak-cover hold target: selling below it would be bought straight back.

    With ``minutes`` (local minute of day): no trim 19:00-05:00, where selling at the
    night/day export rate and rebuying later loses money and would fight the overnight
    charge. Before the 16:00 peak the floor also includes ``plan_target_soc``, the SOC
    tonight's plan bought at the cheap rate to bridge to solar.
    """
    if minutes is not None and not TRIM_FROM_MINUTES <= minutes < PEAK_END_MINUTES:
        return None
    floors = [default, reserve_soc or 0]
    if plan_target_soc is not None and (minutes is None or minutes < PEAK_START_MINUTES):
        floors.append(plan_target_soc)
    if hold_soc is not None:
        floors.append(hold_soc)
    target = max(floors)
    return target if target < soc else None


TRIM_DEFER_MIN_KWH = 0.3  # a deferral smaller than this isn't worth changing the push


def trim_defer_kwh(
    soc: float,
    trim_target_soc_: int,
    capacity_kwh: float,
    remaining_pv_kwh: float | None,
    load_kw: float,
    hours_to_peak: float,
    clipped_today: bool = False,
) -> dict[str, Any]:
    """Split a trim into the kWh that must go now and the kWh that can wait for the peak (phase 65).

    Only the PV left until 16:00 minus the house load, beyond the battery's headroom, would overflow
    (be clipped). Trim just that now; hold the rest to sell 16:00-19:00 at the peak export rate.
    No forecast (None) or solar already clipped today -> defer nothing (trim in full, as before).
    ``needed_soc`` is the target to trim to now (never above ``soc``, never below the full-trim target).
    """
    full_kwh = max(0.0, (soc - trim_target_soc_) / 100.0 * capacity_kwh)
    if remaining_pv_kwh is None or clipped_today or capacity_kwh <= 0:
        return {"full_trim_kwh": round(full_kwh, 2), "needed_kwh": round(full_kwh, 2), "defer_kwh": 0.0,
                "needed_soc": trim_target_soc_, "reason": "no_forecast" if remaining_pv_kwh is None else "clipped_today"}
    surplus = remaining_pv_kwh - max(0.0, load_kw) * max(0.0, hours_to_peak)
    headroom = (100.0 - soc) / 100.0 * capacity_kwh
    overflow = max(0.0, surplus - headroom)
    needed = min(full_kwh, overflow)
    defer = full_kwh - needed
    if defer < TRIM_DEFER_MIN_KWH:
        needed, defer = full_kwh, 0.0
    needed_soc = trim_target_soc_ if defer == 0 else min(100, math.ceil(soc - needed / capacity_kwh * 100.0))
    return {"full_trim_kwh": round(full_kwh, 2), "needed_kwh": round(needed, 2), "defer_kwh": round(defer, 2),
            "needed_soc": max(trim_target_soc_, needed_soc), "reason": "deferred" if defer else "overflow_needs_trim"}


# Phase 17: sell the surplus above the evening reserve 16:00-19:00 (shadow first).
PEAK_EXPORT_MARGIN_SOC = 5


def wear_cost_pence_per_kwh(cost_gbp: float | None, lifetime_kwh: float | None) -> float | None:
    """Battery wear per kWh cycled (phase 24): purchase cost over rated lifetime throughput.

    None when either input is blank or non-positive, so decisions behave as
    before (no wear term) rather than treating a missing cost as 0p."""
    if not cost_gbp or not lifetime_kwh or cost_gbp <= 0 or lifetime_kwh <= 0:
        return None
    return round(cost_gbp / lifetime_kwh * 100, 3)


def arbitrage_worth_it(
    buy_pence: float | None, sell_pence: float | None,
    round_trip_eff: float = ROUND_TRIP_EFFICIENCY, wear_pence: float | None = None,
) -> bool | None:
    """True when selling now beats buying the energy back later after losses
    (and battery wear from phase 24; None = no wear term).
    None when a price is missing — never treat a missing price as 0p."""
    if buy_pence is None or sell_pence is None or round_trip_eff <= 0:
        return None
    return sell_pence > buy_pence / round_trip_eff + (wear_pence or 0.0)


def peak_export_skip_reason(slots_held: bool, soc: float | None, outage_active: bool = False) -> str | None:
    """Why the 16:00 peak export is skipped (logged as its decision), or None to plan it.

    ``outage_active`` (phase 127) is checked first so a grid outage still writes the skip record
    every other early exit writes, instead of leaving the day's peak_export silently empty.
    """
    if outage_active:
        return "skipped_outage"
    if slots_held:
        return "skipped_slots_held"
    if soc is None:
        return "skipped_no_soc"
    return None


def peak_export_plan(
    soc: float,
    reserve_soc: int,
    battery_capacity_kwh: float,
    export_pence: float | None,
    offpeak_pence: float | None,
    margin_soc: int = PEAK_EXPORT_MARGIN_SOC,
    wear_pence: float | None = None,
    peak_need_soc: float = 0.0,
    min_target_soc: int = 0,
    min_target_label: str | None = None,
) -> dict[str, Any]:
    """16:00 decision: export the SOC above reserve + peak need + margin at the peak rate.

    `peak_need_soc` is the SOC the house will use 16:00-19:00 (`peak_need_soc()`),
    so the battery still reaches 19:00 at the evening reserve instead of the
    peak load being bought at the peak rate. The kWh sold would otherwise
    reduce tomorrow's off-peak charge, so it is worth it only if the peak
    export price beats the off-peak replacement cost after round-trip losses.
    `decision` is export / no_surplus / not_worth_it / no_price; gain is export
    income minus replacement cost. `min_target_soc`/`min_target_label` (phase 127,
    from `peak_export_floor_soc`) never let the target sell below the user's Peak
    minimum SOC or today's peak-cover requirement; `floor_applied` names which, if either did.
    """
    target = min(100, math.ceil(reserve_soc + peak_need_soc + margin_soc))
    floor_applied = None
    if min_target_soc > target:
        target = min(100, math.ceil(min_target_soc))
        floor_applied = min_target_label
    surplus_kwh = max(0.0, soc - target) / 100 * battery_capacity_kwh
    worth = arbitrage_worth_it(offpeak_pence, export_pence, wear_pence=wear_pence)
    if worth is None:
        decision = "no_price"
    elif surplus_kwh < PEAK_EXPORT_MIN_KWH:
        decision = "no_surplus"
    elif not worth:
        decision = "not_worth_it"
    else:
        decision = "export"
    exporting = decision == "export"
    export_gbp = gain_gbp = None
    if exporting:
        export_gbp = surplus_kwh * export_pence / 100
        gain_gbp = export_gbp - surplus_kwh * (offpeak_pence / ROUND_TRIP_EFFICIENCY + (wear_pence or 0.0)) / 100
    return {
        "decision": decision,
        "target_soc": target,
        "export_kwh": round(surplus_kwh, 2) if exporting else 0.0,
        "export_gbp": round_or_none(export_gbp),
        "gain_gbp": round_or_none(gain_gbp),
        "wear_cost_p": wear_pence,
        "floor_applied": floor_applied,
    }


PEAK_RECHECK_MIN_KWH = 0.5  # smallest target move (kWh) worth a mid-peak push (phase 66)

PEAK_RECHECK_MARGIN = 1.1  # on the observed rate: a spike late in the window must not be bought at the peak rate


def peak_export_recheck(
    soc: float | None,
    reserve_soc: int,
    battery_capacity_kwh: float,
    elapsed_minutes: float,
    minutes_left: float,
    observed_peak_kwh: float | None,
    prior_target_soc: int | None,
    prior_pushed: bool,
    export_pence: float | None,
    offpeak_pence: float | None,
    wear_pence: float | None = None,
    min_target_soc: int = 0,
    min_target_label: str | None = None,
) -> dict[str, Any]:
    """17:00/18:00 re-check of the peak export target from the load seen since 16:00 (phase 66).

    The 16:00 plan sized the peak load from history. The rest of the window is
    sized from the observed rate instead (x PEAK_RECHECK_MARGIN) and run through
    `peak_export_plan`, so the target never falls below reserve + margin. A push
    is proposed only when it moves the target by >= PEAK_RECHECK_MIN_KWH versus
    the 16:00 target: lower (sell more) when the load is lighter than assumed,
    higher (stop selling) only if the 16:00 plan was actually pushed. Missing
    soc or observed load is None, never 0. `min_target_soc`/`min_target_label`
    (phase 127) apply the same floor as the 16:00 plan.
    """
    base: dict[str, Any] = {
        "decision": None, "action": None, "target_soc": None, "prior_target_soc": prior_target_soc,
        "move_kwh": None, "export_kwh": 0.0, "gain_gbp": None, "floor_applied": None,
        "observed_peak_kwh": observed_peak_kwh, "remaining_load_kwh": None,
    }
    if soc is None:
        return {**base, "decision": "skipped_no_soc"}
    if observed_peak_kwh is None or observed_peak_kwh < 0 or elapsed_minutes <= 0:
        return {**base, "decision": "skipped_no_observed_load"}
    remaining_kwh = observed_peak_kwh / (elapsed_minutes / 60.0) * max(0.0, minutes_left) / 60.0
    need = peak_need_soc(remaining_kwh, battery_capacity_kwh, margin=PEAK_RECHECK_MARGIN)
    plan = peak_export_plan(
        soc, reserve_soc, battery_capacity_kwh, export_pence, offpeak_pence,
        wear_pence=wear_pence, peak_need_soc=need,
        min_target_soc=min_target_soc, min_target_label=min_target_label,
    )
    target = plan["target_soc"]
    move = None if prior_target_soc is None else round((prior_target_soc - target) / 100.0 * battery_capacity_kwh, 2)
    action = None
    if plan["decision"] == "export" and (move is None or move >= PEAK_RECHECK_MIN_KWH):
        action = "lower"
    elif prior_pushed and move is not None and -move >= PEAK_RECHECK_MIN_KWH:
        action = "raise"
    return {
        **base, "decision": plan["decision"], "action": action, "target_soc": target, "move_kwh": move,
        "export_kwh": plan["export_kwh"], "gain_gbp": plan["gain_gbp"], "floor_applied": plan["floor_applied"],
        "remaining_load_kwh": round(remaining_kwh, 2),
    }


# Phase 30: peak cover — make sure the battery reaches 16:00 with enough charge
# to run the house through 16:00-19:00, topping up at the (cheaper) day rate.
PEAK_COVER_FLOOR_SOC = BATTERY_LOW_SOC  # full-power discharge through the peak

PEAK_COVER_MIN_SHORTFALL_SOC = 3

PEAK_COVER_LEAD_MINUTES = 45  # start this long before the charge needs to begin

PEAK_COVER_HOLD_FROM_MINUTES = 9 * 60  # a hold (not a top-up) may start this early (phase 59)


PEAK_COVER_TOPUP_BUFFER_KWH = 1.5  # phase 132: fixed allowance over a typical evening (cooking, guests)


def peak_need_soc(
    peak_load_kwh: float, capacity_kwh: float,
    margin: float = PEAK_COVER_MARGIN, efficiency: float = DISCHARGE_EFFICIENCY, buffer_kwh: float = 0.0,
) -> float:
    """SOC points the 16:00-19:00 house load (times margin, plus buffer_kwh) takes out of the battery after discharge losses."""
    return (peak_load_kwh * margin + buffer_kwh) / efficiency / capacity_kwh * 100.0


def peak_cover_worth_it(
    day_pence: float | None, peak_pence: float | None, efficiency: float = CHARGE_EFFICIENCY,
    wear_pence: float | None = None,
) -> bool | None:
    """True when charging at the day rate beats buying the same kWh at the peak rate after charge losses and wear.
    None when a price is missing — never treat a missing price as 0p. Unknown wear counts as none."""
    if day_pence is None or peak_pence is None or efficiency <= 0:
        return None
    return peak_pence > day_pence / efficiency + (wear_pence or 0.0)


def peak_cover_plan(
    soc: float,
    capacity_kwh: float,
    minutes_to_peak: float,
    solar_kwh: float,
    load_kw: float,
    peak_load_kwh: float,
    charge_rate_kw: float,
    day_pence: float | None,
    peak_pence: float | None,
    floor_soc: int = PEAK_COVER_FLOOR_SOC,
    margin: float = PEAK_COVER_MARGIN,
    wear_pence: float | None = None,
    min_soc: float | None = None,
    buffer_kwh: float = 0.0,
) -> dict[str, Any]:
    """Will the battery reach 16:00 with enough to run the peak window? If not, how to top up.

    `min_soc` (phase 122, the user's "Peak minimum SOC"; None or 0 = off) raises the required SOC.

    Projects SOC at 16:00 from the current SOC, forecast solar and house load
    (a battery that would run flat sits at the floor, the grid covering the
    rest), against the SOC needed to carry the expected peak load above the
    floor. decision: ok / hold / top_up / not_worth_it / no_price. A hold (phase 59) is the same slot
    pushed while the SOC is still at or above the requirement. A top-up is a Flux 1
    slot now->16:00 at target_soc: the inverter only imports while below the
    target, so it holds the battery there at the day rate. `push_now` waits
    until the charge would need to start (plus a lead) so later solar can still
    help. Gain is the peak cost avoided minus the day-rate cost of the top-up.
    """
    hours = max(0.0, minutes_to_peak / 60.0)
    projected = soc + (solar_kwh - load_kw * hours) / capacity_kwh * 100.0
    projected_soc = round(min(100.0, max(float(floor_soc), projected)), 1)
    required_soc = peak_required_soc(
        floor_soc + peak_need_soc(peak_load_kwh, capacity_kwh, margin, buffer_kwh=buffer_kwh), min_soc
    )
    shortfall_soc = max(0.0, required_soc - projected_soc)
    worth = peak_cover_worth_it(day_pence, peak_pence, wear_pence=wear_pence)
    # Phase 59: still at or above the required SOC but heading below it by 16:00. Holding the
    # battery there (the grid runs the house at the day rate) avoids recharging it later through
    # the charge loss and an extra cycle. No wear or loss term: nothing is stored, so it is worth
    # doing whenever the peak rate beats the day rate.
    holding = (
        day_pence is not None and peak_pence is not None
        and soc >= required_soc and shortfall_soc >= PEAK_COVER_MIN_SHORTFALL_SOC and peak_pence > day_pence
    )
    if worth is None:
        decision = "no_price"
    elif shortfall_soc < PEAK_COVER_MIN_SHORTFALL_SOC:
        decision = "ok"
    elif holding:
        decision = "hold"
    elif not worth:
        decision = "not_worth_it"
    else:
        decision = "top_up"
    topping = decision == "top_up"
    held = decision == "hold"
    topup_kwh = round(shortfall_soc / 100.0 * capacity_kwh, 2) if (topping or held) else 0.0
    charge_minutes = math.ceil(topup_kwh / CHARGE_EFFICIENCY / charge_rate_kw * 60.0) if topping and charge_rate_kw > 0 else 0
    gain_gbp = gain_after_wear_gbp = gain_vs_topup_gbp = None
    if topping:
        gain_gbp = round_or_none(topup_kwh * (peak_pence - day_pence / CHARGE_EFFICIENCY) / 100.0)
        gain_after_wear_gbp = round_or_none(topup_kwh * (peak_pence - day_pence / CHARGE_EFFICIENCY - (wear_pence or 0.0)) / 100.0)
    elif held:
        gain_gbp = gain_after_wear_gbp = round_or_none(topup_kwh * (peak_pence - day_pence) / 100.0)
        # What holding saves against recharging the same kWh later: the charge loss plus the cycle's wear.
        gain_vs_topup_gbp = round_or_none(topup_kwh * (day_pence * (1.0 / CHARGE_EFFICIENCY - 1.0) + (wear_pence or 0.0)) / 100.0)
    return {
        "decision": decision,
        "projected_soc_16": projected_soc,
        "required_soc": required_soc,
        "shortfall_soc": round(shortfall_soc, 1),
        "target_soc": required_soc if (topping or held) else None,
        "topup_kwh": topup_kwh,
        "charge_minutes": charge_minutes,
        "push_now": held or (topping and minutes_to_peak <= charge_minutes + PEAK_COVER_LEAD_MINUTES),
        "gain_gbp": gain_gbp,
        "gain_after_wear_gbp": gain_after_wear_gbp,
        "gain_vs_topup_gbp": gain_vs_topup_gbp,
        "peak_min_soc": int(min_soc) if _is_number(min_soc) and min_soc > 0 else None,
    }


def peak_cover_plan_split(
    soc: float,
    capacity_kwh: float,
    minutes_to_peak: float,
    solar_kwh: float,
    load_kw: float,
    peak_load_kwh: float,
    typical_load_kwh: float | None,
    charge_rate_kw: float,
    day_pence: float | None,
    peak_pence: float | None,
    margin: float = PEAK_COVER_MARGIN,
    buffer_kwh: float = PEAK_COVER_TOPUP_BUFFER_KWH,
    wear_pence: float | None = None,
    min_soc: float | None = None,
    floor_soc: int = PEAK_COVER_FLOOR_SOC,
) -> dict[str, Any]:
    """Peak cover with a generous hold and a lean top-up (phase 132).

    A hold against the cautious estimate (`peak_load_kwh` x `margin`) costs nothing: the charge
    left at 19:00 is used afterwards at the same day rate. A top-up buys charge through the
    charge loss, so anything short of an `ok` or `hold` is re-planned on the typical evening
    (`typical_load_kwh`, no margin) plus a fixed `buffer_kwh`. That re-plan may itself hold
    (at the lean requirement) or top up. `typical_load_kwh` None keeps the cautious plan.
    `required_soc_hold` is the cautious requirement; `required_basis` says which one was used.
    """
    args = (soc, capacity_kwh, minutes_to_peak, solar_kwh, load_kw)
    plan = peak_cover_plan(
        *args, peak_load_kwh, charge_rate_kw, day_pence, peak_pence,
        floor_soc=floor_soc, margin=margin, wear_pence=wear_pence, min_soc=min_soc,
    )
    out, basis = plan, "hold"
    if plan["decision"] not in ("ok", "hold") and _is_number(typical_load_kwh):
        out = peak_cover_plan(
            *args, typical_load_kwh, charge_rate_kw, day_pence, peak_pence,
            floor_soc=floor_soc, margin=1.0, wear_pence=wear_pence, min_soc=min_soc, buffer_kwh=buffer_kwh,
        )
        basis = "typical"
    return {**out, "required_soc_hold": plan["required_soc"], "required_basis": basis}


def peak_required_soc(estimated_soc: float, min_soc: float | None = None) -> int:
    """SOC needed at 16:00 (phase 122): the load estimate, never below the user's minimum (None or 0 = off)."""
    required = math.ceil(estimated_soc)
    if _is_number(min_soc) and min_soc > 0:
        required = max(required, math.ceil(float(min_soc)))
    return min(100, required)


# ---- Phase 86: hourly profile and reserve-hold review (shadow) ------------------------------------------
RESERVE_HOLD_MARGIN_SOC = 3.0  # within this of the floor counts as "at the floor"


def _hour_band(hour: int) -> str:
    """Flux band of a clock hour: night 02-05, peak 16-19, else day."""
    return "offpeak" if 2 <= hour < 5 else "peak" if 16 <= hour < 19 else "day"


def reserve_hold_cost(
    hourly: list[dict[str, Any]], floor_soc: float, prices_pence: dict[str, float | None]
) -> dict[str, Any]:
    """Import bought while the battery still held charge, against import after it ran out.

    Splits each hour's grid import by band and by whether the battery started the hour above the
    floor (+ RESERVE_HOLD_MARGIN_SOC). ``above_floor_day_import_kwh`` is the figure to watch: day-rate
    energy bought with charge in hand, which a different hold could have avoided. Missing → None.
    """
    out = {"above_floor_day_import_kwh": None, "at_floor_day_import_kwh": None, "peak_import_kwh": None,
           "above_floor_day_import_gbp": None}
    for row in hourly:
        kwh, soc = row.get("grid_import_kwh"), row.get("soc")
        if not _is_number(kwh) or not _is_number(soc) or kwh <= 0:
            continue
        band = _hour_band(int(row["hour"]))
        if band == "peak":
            key = "peak_import_kwh"
        elif band == "day":
            key = "above_floor_day_import_kwh" if soc > floor_soc + RESERVE_HOLD_MARGIN_SOC else "at_floor_day_import_kwh"
        else:
            continue
        out[key] = round((out[key] or 0.0) + kwh, 3)
    day = prices_pence.get("day")
    if out["above_floor_day_import_kwh"] is not None and day is not None:
        out["above_floor_day_import_gbp"] = round(out["above_floor_day_import_kwh"] * day / 100, 2)
    return out


PEAK_COVER_STARTUP_RETRIES = 5  # 60 s apart, like the initial plan's wait for the first sensor poll


def peak_cover_startup_retry_due(
    minutes: int, soc, outage_active: bool, attempts: int, peak_start: int = PEAK_COVER_PEAK_START_MINUTES,
) -> bool:
    """Re-try peak cover a minute later after a restart (the startup plan wiped its Flux 1 slot).

    Only inside the peak-cover window (09:00 to the no-buy start, normally 16:00), only while SOC is not
    yet readable or a grid outage is still flagged, and at most PEAK_COVER_STARTUP_RETRIES times."""
    in_window = PEAK_COVER_HOLD_FROM_MINUTES <= minutes < peak_start
    return in_window and (soc is None or outage_active) and attempts < PEAK_COVER_STARTUP_RETRIES


# ---- Review fixes (21/11/2026, phase 127) ----------------------------------------------------------

PEAK_COVER_PUSH_RETRIES = 3  # a transient API failure retries this many times before giving up for the day


def peak_cover_retry_due(
    api_ok: bool | None, minutes: int, attempts: int, last_log: int = PEAK_COVER_LAST_LOG_MINUTES,
) -> bool:
    """True when a failed, live, actionable peak-cover push should retry on the next 30-min check.

    api_ok is None (not live/not actionable) or True (pushed, or nothing to push) never retries.
    A failure retries until PEAK_COVER_PUSH_RETRIES attempts or PEAK_COVER_LAST_LOG_MINUTES,
    whichever comes first, so a failing day still gets its final decision logged once (phase 127).
    """
    if api_ok is not False:
        return False
    return attempts < PEAK_COVER_PUSH_RETRIES and minutes < last_log


# ---- Phase 135: an earlier no-buy start on demand (e.g. a saving session before the peak) ---------------


def no_buy_start_minutes(no_buy_from: datetime | None, today: date) -> int:
    """The day's no-buy start (minutes past midnight): the user's earlier start on its own day, else 16:00."""
    if no_buy_from is None or no_buy_from.date() != today:
        return PEAK_COVER_PEAK_START_MINUTES
    minutes = no_buy_from.hour * 60 + no_buy_from.minute
    return minutes if minutes < PEAK_COVER_PEAK_START_MINUTES else PEAK_COVER_PEAK_START_MINUTES


def no_buy_extra_load_kwh(start_minutes: int, load_kw: float | None) -> float:
    """House load from an earlier no-buy start to 16:00, which the battery must now carry as well."""
    hours = max(0, PEAK_COVER_PEAK_START_MINUTES - start_minutes) / 60.0
    return round(hours * max(0.0, load_kw or 0.0), 2)


def peak_cover_last_log_minutes(start_minutes: int) -> int:
    """The last-check log time moves with the no-buy start (normally 15:30 for 16:00)."""
    return start_minutes - (PEAK_COVER_PEAK_START_MINUTES - PEAK_COVER_LAST_LOG_MINUTES)


def peak_export_floor_soc(peak_min_soc: int | None, peak_cover_required_soc: float | None) -> tuple[int, str | None]:
    """The higher of the user's Peak minimum SOC and today's peak-cover requirement (phase 127
    user decision), as a floor for the 16:00 peak export target, so the export never sells below
    what peak cover just topped up to. Returns (floor, which one — None if neither is set); the
    caller applies it only when it exceeds the target the plan would otherwise pick.
    """
    floors: list[tuple[int, str | None]] = [(0, None)]
    if _is_number(peak_min_soc) and peak_min_soc > 0:
        floors.append((math.ceil(peak_min_soc), "peak_min_soc"))
    if _is_number(peak_cover_required_soc):
        floors.append((math.ceil(peak_cover_required_soc), "peak_cover_required_soc"))
    return max(floors, key=lambda f: f[0])
