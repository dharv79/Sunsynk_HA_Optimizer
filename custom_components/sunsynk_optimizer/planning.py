# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Pure planning maths for the optimizer — no Home Assistant imports.

Everything here takes plain values and returns plain values so it can be
unit-tested without a running HA instance (see tests/test_planning.py).
optimizer.py gathers the inputs from HA state/config and calls into these.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

# Below this (pessimistic) daily forecast the plan switches to max import:
# 95-100% target and the full 02:00-05:00 window.
LOW_SOLAR_THRESHOLD_KWH = 7.0

# Hours walked by the solar-bridge models (05:00 up to and including 12:00).
_BRIDGE_WALK_HOURS = range(5, 13)

_CONDITION_ADJUSTMENTS = {
    "sunny": 25,
    "clear": 25,
    "partlycloudy": 10,
    "cloudy": -10,
    "fog": -10,
    "rainy": -25,
    "pouring": -25,
    "lightning-rainy": -25,
    "snowy": -25,
    "snowy-rainy": -25,
}

# Later-in-week penalty: filling up on Thursday/Friday leaves less of the week
# to use the stored energy before the weekend.
_DAY_PENALTIES = {"Thursday": 5, "Friday": 15}


def forecast_band(forecast_kwh: float) -> str:
    """Classify a daily solar forecast into the seasonal band used for SOC targets."""
    if forecast_kwh >= 10:
        return "summer_like"
    if forecast_kwh <= 5:
        return "winter_like"
    return "shoulder"


def bridge_soc(energy_kwh: float, battery_capacity_kwh: float) -> int:
    """SOC% needed to cover `energy_kwh` of load on top of a 20% floor, clamped 30-100."""
    return max(30, min(100, int(20 + energy_kwh / battery_capacity_kwh * 100)))


def walk_bridge_gap(
    hourly_kwh: dict[int, float], avg_consumption_kw: float, scale: float = 1.0
) -> tuple[float, int | None]:
    """Walk 05:00-12:00 accumulating the load deficit until solar covers load.

    Returns (energy_gap_kwh, bridge_hour) where bridge_hour is the first hour
    whose (scaled) solar meets the average load, or None if none does.
    """
    gap = 0.0
    for h in _BRIDGE_WALK_HOURS:
        solar_h = hourly_kwh.get(h, 0.0) * scale
        if solar_h >= avg_consumption_kw:
            return gap, h
        gap += max(0.0, avg_consumption_kw - solar_h)
    return gap, None


def synthetic_hourly_profile(daily_kwh: float, sunrise_h: float, sunset_h: float) -> dict[int, float] | None:
    """Spread a daily kWh total across daylight hours as a sine bell.

    Zero at sunrise/sunset, peak at solar noon. Lets the solar-bridge walk
    model the morning ramp for users without a real hourly forecast. Returns
    None if the inputs can't produce a profile.
    """
    if daily_kwh <= 0 or sunset_h <= sunrise_h:
        return None
    daylight = sunset_h - sunrise_h
    shapes = {}
    for h in range(24):
        frac = (h + 0.5 - sunrise_h) / daylight  # this hour's midpoint within the day
        shapes[h] = math.sin(math.pi * frac) if 0.0 < frac < 1.0 else 0.0
    total = sum(shapes.values())
    if total <= 0:
        return None
    scale = daily_kwh / total
    return {h: s * scale for h, s in shapes.items()}


@dataclass
class TargetDecision:
    """Outcome of select_target_soc, before drain/evening-nudge adjustments."""

    target_soc: int
    reason: str
    hourly_forecast_used: bool = False
    synthetic_ramp: bool = False
    bridge_hour: int | None = None


def select_target_soc(
    *,
    is_full_day: bool,
    low_solar_forecast_kwh: float,
    band: str,
    hours_to_solar: float | None,
    avg_consumption_kw: float,
    battery_capacity_kwh: float,
    hourly_forecast_kwh: dict[int, float] | None = None,
    hourly_correction: float = 1.0,
    synthetic_profile: dict[int, float] | None = None,
) -> TargetDecision:
    """Pick the overnight target SOC. See CLAUDE.md "SOC target selection".

    hours_to_solar is None when sun.sun is unavailable (no solar start time).
    synthetic_profile is the sine-bell ramp from the RAW daily forecast, used
    only as a safety net that can raise (never lower) the simple bridge.
    """
    low_solar = low_solar_forecast_kwh < LOW_SOLAR_THRESHOLD_KWH

    if is_full_day:
        if not low_solar and hours_to_solar is not None:
            # Bridge to solar start; PV charges to 100% during the day for free.
            return TargetDecision(
                bridge_soc(hours_to_solar * avg_consumption_kw, battery_capacity_kwh), "weekly_full_charge_day"
            )
        # Poor solar on the chosen day — import fully from grid.
        return TargetDecision(100, "weekly_full_charge_day")

    if low_solar:
        if band == "winter_like":
            return TargetDecision(100, "low_solar_override_winter_like")
        return TargetDecision(95, "low_solar_override")

    if hourly_forecast_kwh:
        gap, bridge_hour = walk_bridge_gap(hourly_forecast_kwh, avg_consumption_kw, hourly_correction)
        return TargetDecision(
            bridge_soc(gap, battery_capacity_kwh), "solar_bridge_hourly",
            hourly_forecast_used=True, bridge_hour=bridge_hour,
        )

    if hours_to_solar is not None:
        # Simple bridge assumes solar covers load the instant the sun is up; the
        # synthetic ramp tops it up on slow-ramp mornings.
        decision = TargetDecision(
            bridge_soc(hours_to_solar * avg_consumption_kw, battery_capacity_kwh), "solar_bridge"
        )
        if synthetic_profile is not None:
            gap, decision.bridge_hour = walk_bridge_gap(synthetic_profile, avg_consumption_kw)
            ramp = bridge_soc(gap, battery_capacity_kwh)
            if ramp > decision.target_soc:
                decision.target_soc = ramp
                decision.reason = "solar_bridge_ramp"
                decision.synthetic_ramp = True
        return decision

    # sun.sun unavailable — band-based fallback.
    return TargetDecision({"winter_like": 95, "summer_like": 80}.get(band, 85), band)


def apply_soc_adjustments(target_soc: int, drain_adjustment: int, evening_adjustment: int) -> int:
    """Add the drain buffer and evening nudge, keeping the estimated 06:00 SOC >= 25%."""
    target = max(20, min(100, target_soc + drain_adjustment + evening_adjustment))
    if drain_adjustment > 0 and target - drain_adjustment < 25:
        target = min(100, 25 + drain_adjustment)
    return target


def temp_deration_factor(battery_temp_c: float) -> float:
    """Charge-rate multiplier for cold batteries (BMS limits charge current when cold)."""
    if battery_temp_c > 15:
        return 1.0
    if battery_temp_c > 10:
        return 0.85
    if battery_temp_c > 5:
        return 0.70
    return 0.55


def resolve_used_charge_rate(
    config_rate_kw: float, effective_rate_kw: float | None, battery_temp_c: float
) -> tuple[float, float]:
    """Return (used_charge_rate_kw, temp_deration_factor).

    The learned rate replaces nameplate only when it's meaningfully (>10%)
    lower; either way the result is then derated for battery temperature.
    """
    rate = config_rate_kw
    if effective_rate_kw is not None and effective_rate_kw < config_rate_kw * 0.9:
        rate = min(config_rate_kw, effective_rate_kw)
    factor = temp_deration_factor(battery_temp_c)
    return round(rate * factor, 2), factor


def flux1_end_minutes(energy_needed_kwh: float, charge_rate_kw: float) -> int:
    """Minutes after midnight the 02:00 import window should end.

    Rounded up to the next 15-minute slot, clamped to 02:15-05:00 (anything
    shorter than 15 minutes isn't worth the API call).
    """
    raw_minutes = (energy_needed_kwh / charge_rate_kw) * 60
    quarter_slots = int((raw_minutes + 14) // 15)
    return max(2 * 60 + 15, min(5 * 60, 2 * 60 + quarter_slots * 15))


# Share of imported AC kWh that lands as battery SOC at the nameplate charge
# rate (inverter conversion plus cell losses).
CHARGE_EFFICIENCY = 0.92
FLUX1_START_MINUTES = 2 * 60


def charge_efficiency(config_rate_kw: float, effective_rate_kw: float | None) -> float:
    """Losses to apply when sizing Flux 1.

    The learned rate is measured as SOC gained per window hour, so it already
    embeds losses; only the nameplate rate needs the efficiency factor. Same
    "meaningfully lower" test as `resolve_used_charge_rate`.
    """
    if effective_rate_kw is not None and effective_rate_kw < config_rate_kw * 0.9:
        return 1.0
    return CHARGE_EFFICIENCY


def window_grid_kwh(
    energy_needed_kwh: float, efficiency: float, window_load_kw: float, end_minutes: int
) -> tuple[float, float]:
    """Return (window_load_kwh, grid_kwh_needed) for a 02:00→end window.

    House load during the window is served from the grid in parallel with the
    battery charge, so it adds grid kWh but not window time (the drain
    adjustment already covers charge-end to 06:00). Losses lengthen the window
    via `flux1_end_minutes(energy_needed_kwh / efficiency, rate)`.
    """
    window_load_kwh = window_load_kw * max(0, end_minutes - FLUX1_START_MINUTES) / 60
    return round(window_load_kwh, 2), round(energy_needed_kwh / efficiency + window_load_kwh, 2)


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_to_minutes(hhmm: str) -> int | None:
    """Minutes after midnight for an "HH:MM" string, or None when malformed."""
    try:
        hours, minutes = str(hhmm).split(":")
        return int(hours) * 60 + int(minutes)
    except (ValueError, TypeError):
        return None


CHARGE_WATCHDOG_MINUTES = 2 * 60 + 20  # 20 min into the 02:00 import window
CHARGE_WATCHDOG_RECHECK_MINUTES = 15
# Progress counts as "ok" once SOC has gained this share of the expected rise
# (the charge ramps up and house load eats into it), with a 1% floor so SOC
# rounding alone never passes; or while grid import is at least this share of
# the expected charge power (SOC can lag the cloud reading by a poll or two).
CHARGE_WATCHDOG_SOC_SHARE = 0.25
CHARGE_WATCHDOG_MIN_SOC_GAIN = 1.0
CHARGE_WATCHDOG_IMPORT_SHARE = 0.5


def charge_progress_ok(
    start_soc: float | None,
    now_soc: float | None,
    minutes: float,
    expected_rate_kw: float | None,
    capacity_kwh: float,
    grid_import_w: float | None,
    target_soc: float | None = None,
) -> str:
    """Judge whether the overnight Flux 1 charge is running: ok | stalled | unknown.

    "unknown" (missing sensors or rate, no elapsed time) must never trigger
    an action. Reaching the target counts as ok — the inverter stops charging.
    """
    if start_soc is None or now_soc is None or grid_import_w is None or not expected_rate_kw or minutes <= 0:
        return "unknown"
    if target_soc is not None and now_soc >= target_soc:
        return "ok"
    expected_gain = expected_rate_kw * minutes / 60.0 / max(0.1, capacity_kwh) * 100.0
    needed_gain = max(CHARGE_WATCHDOG_MIN_SOC_GAIN, expected_gain * CHARGE_WATCHDOG_SOC_SHARE)
    if now_soc - start_soc >= needed_gain:
        return "ok"
    if grid_import_w >= expected_rate_kw * 1000 * CHARGE_WATCHDOG_IMPORT_SHARE:
        return "ok"
    return "stalled"


STARTUP_PLAN_SOURCE = "startup"
NIGHTLY_PLAN_MINUTES = 1 * 60 + 55


def should_log_import_plan(source: str, now: datetime) -> bool:
    """Whether a plan run is tonight's record for the JSONL log.

    The log keeps the first plan per date, so a startup/reload plan before
    01:55 would displace the real nightly plan (and its 01:55 SOC) from
    pairing. Later startup plans are harmless: the 01:55 record already won,
    or — if HA was down at 01:55 — it is the plan the inverter actually ran.
    """
    if source != STARTUP_PLAN_SOURCE:
        return True
    return now.hour * 60 + now.minute >= NIGHTLY_PLAN_MINUTES


def daily_report_plans(nightly: dict[str, Any], latest: dict[str, Any], date: str) -> list[dict[str, Any]]:
    """Plan lines for the 22:00 data report: tonight's 01:55 plan first.

    A later startup/reload re-plan is appended rather than replacing it, so
    the report shows the plan that drove the overnight charge.
    """
    plans = [p for p in (nightly, latest) if p and p.get("date") == date]
    if len(plans) == 2 and plans[0] == plans[1]:
        plans = plans[:1]
    return plans


def score_full_charge_day(item: dict[str, Any], day_name: str) -> float:
    """Score one weekday's weather forecast for full-charge suitability."""
    cloud = float(item.get("cloud_coverage", 50) or 50)
    rain = float(item.get("precipitation_probability", 0) or 0)
    temp = float(item.get("temperature", 15) or 15)
    # Rain weighted 0.7x because partial rain still allows some generation.
    score = 100 - cloud - (rain * 0.7)
    score += _CONDITION_ADJUSTMENTS.get(str(item.get("condition", "unknown")), 0)
    # Warmer days tend to have longer usable solar hours.
    if temp >= 18:
        score += 3
    elif temp <= 5:
        score -= 3
    score -= _DAY_PENALTIES.get(day_name, 0)
    return round(score, 1)


# Daylight hours of house load the PV must cover on top of filling the battery
# for a full-charge day to reach 100% from solar rather than grid.
FULL_CHARGE_DAYLIGHT_LOAD_HOURS = 8.0


def solar_kwh_to_fill(battery_capacity_kwh: float, avg_consumption_kw: float) -> float:
    """Conservative PV kWh needed to fill the battery from empty plus daytime load."""
    return round(battery_capacity_kwh + avg_consumption_kw * FULL_CHARGE_DAYLIGHT_LOAD_HOURS, 2)


def full_charge_day_move(
    *,
    tomorrow: str,
    chosen: str,
    tomorrow_kwh: float | None,
    needed_kwh: float,
    tomorrow_score: float | None,
    chosen_score: float | None,
    already_moved: bool,
    weekdays: list[str],
) -> tuple[bool, str]:
    """Daily re-check: move this week's full-charge day to tomorrow?

    Moves only when tomorrow's kWh forecast says PV alone can reach 100% and
    tomorrow's weather scores better than the chosen day's, at most once a
    week and never to a day after the chosen one has already passed.
    Returns (move, reason); missing inputs never move.
    """
    if tomorrow not in weekdays or chosen not in weekdays:
        return False, "not_weekday"
    if tomorrow == chosen:
        return False, "already_chosen"
    if weekdays.index(chosen) < weekdays.index(tomorrow):
        return False, "chosen_passed"
    if already_moved:
        return False, "already_moved"
    if tomorrow_kwh is None:
        return False, "no_forecast"
    if tomorrow_kwh < needed_kwh:
        return False, "not_enough_solar"
    if tomorrow_score is None or chosen_score is None:
        return False, "no_weather"
    if tomorrow_score <= chosen_score:
        return False, "chosen_looks_better"
    return True, "moved"


# Phase 20: recency-weighted, per-band forecast correction. Logged beside the
# live 30-day median (shadow) until the phase 14 backtest validates it.
FORECAST_CORRECTION_HALF_LIFE_DAYS = 14.0
FORECAST_CORRECTION_MIN_DAYS = 7


def weighted_forecast_correction(
    paired_days: list[dict[str, Any]],
    today: date,
    band: str | None = None,
    half_life_days: float = FORECAST_CORRECTION_HALF_LIFE_DAYS,
    min_days: int = FORECAST_CORRECTION_MIN_DAYS,
) -> tuple[float, str]:
    """Return (factor, basis): weighted median of actual/forecast, capped 0.5-3.0.

    Each day weighs 0.5 ** (age / half_life), so a season change shows up in
    about two weeks instead of the plain median's ~15 days of lag. Uses only
    days in ``band`` when it has ``min_days``, else all days ("global"); below
    ``min_days`` overall returns (1.0, "none"). Same ratio and near-zero skip
    as data_logger.compute_forecast_correction.
    """
    valid = []
    for d in paired_days:
        forecast = d.get("solar_forecast_kwh") or 0.0
        if forecast <= 0.5 or d.get("actual_solar_kwh") is None:
            continue
        try:
            age = max(0, (today - date.fromisoformat(d["date"])).days)
        except (KeyError, TypeError, ValueError):
            continue
        valid.append((d["actual_solar_kwh"] / forecast, 0.5 ** (age / half_life_days), d.get("forecast_band")))
    if len(valid) < min_days:
        return 1.0, "none"
    basis = "global"
    in_band = [v for v in valid if band is not None and v[2] == band]
    if len(in_band) >= min_days:
        valid, basis = in_band, "band"
    valid.sort(key=lambda v: v[0])
    half = sum(w for _, w, _ in valid) / 2.0
    running = 0.0
    factor = valid[-1][0]
    for ratio, weight, _ in valid:
        running += weight
        if running >= half:
            factor = ratio
            break
    return max(0.5, min(3.0, round(factor, 3))), basis


# Phase 12: overnight load rate learned from the 06:00 morning_state reading
# (SolarSynkV3 daily load at 06:00 = 00:00-06:00 household use).
OVERNIGHT_LOAD_HOURS = 6.0
LEARNED_LOAD_WINDOW_DAYS = 28
LEARNED_LOAD_MIN_DAYS = 7
LEARNED_LOAD_CLAMP = (0.5, 2.0)


def learned_load_kw(
    paired_days: list[dict[str, Any]],
    today: date,
    weekend: bool | None = None,
    window_days: int = LEARNED_LOAD_WINDOW_DAYS,
    min_days: int = LEARNED_LOAD_MIN_DAYS,
) -> tuple[float | None, int]:
    """Return (median overnight kW, days used), or (None, n) below ``min_days``.

    Home days only (away days are a different regime — do-not-break 18), no
    full-charge days, and no day with a missing or zero load reading (a meter
    unavailable at 06:00 must not drag the rate down). With ``weekend`` set,
    uses only matching days when there are ``min_days`` of them, else all.
    """
    rates: list[tuple[float, bool]] = []
    for d in paired_days:
        load = d.get("overnight_load_kwh")
        if d.get("away") or d.get("is_full_day") or not load or load <= 0:
            continue
        try:
            day = date.fromisoformat(d["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 < (today - day).days <= window_days:
            continue
        rates.append((load / OVERNIGHT_LOAD_HOURS, day.weekday() >= 5))
    if weekend is not None:
        matching = [r for r in rates if r[1] == weekend]
        if len(matching) >= min_days:
            rates = matching
    if len(rates) < min_days:
        return None, len(rates)
    values = sorted(r for r, _ in rates)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0
    return round(median, 3), len(values)


def resolve_load_kw(config_kw: float, learned_kw: float | None) -> tuple[float, str]:
    """Return (rate used, source): learned, clamped to 0.5x-2x config, else config."""
    if learned_kw is None:
        return config_kw, "config"
    low, high = LEARNED_LOAD_CLAMP
    return round(max(config_kw * low, min(config_kw * high, learned_kw)), 3), "learned"


# Phase 14: cumulative daily-meter snapshots at the Flux band edges. The
# SolarSynkV3 daily totals reset at midnight, so 22:00 closes the KPI day
# (22:00-24:00 is not covered, same as day_actuals).
METER_SNAPSHOT_TIMES = ("02:00", "05:00", "16:00", "19:00", "22:00")
KPI_RESERVE_SOC = 20  # same floor as bridge_soc
KPI_LATE_LOAD_HOURS = 4.0  # 22:00 → next 02:00 charge
DAY_KPI_FIELDS = (
    "grid_import_offpeak_kwh",
    "grid_import_day_kwh",
    "grid_import_morning_kwh",
    "grid_import_peak_kwh",
    "grid_import_evening_kwh",
    "self_sufficiency_pct",
    "avoidable_import_gbp",
    "export_peak_kwh",
    "export_peak_gbp",
    "unused_charge_kwh",
)


def _snap(snapshots: dict[str, dict[str, Any]], time: str, key: str) -> float | None:
    value = (snapshots.get(time) or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _snap_delta(snapshots: dict[str, dict[str, Any]], start: str, end: str, key: str) -> float | None:
    """Meter increase between two snapshots; None if either is missing or it went backwards."""
    a, b = _snap(snapshots, start, key), _snap(snapshots, end, key)
    if a is None or b is None or b < a:
        return None
    return b - a


def _mul(*values: float | None) -> float | None:
    if any(v is None for v in values):
        return None
    return math.prod(values)


def day_kpis(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    battery_capacity_kwh: float,
    late_load_kw: float | None,
) -> dict[str, float | None]:
    """Efficiency KPIs for 00:00-22:00 from the day's meter snapshots.

    `snapshots` maps "HH:MM" → {grid_import_kwh, grid_export_kwh, load_kwh, soc}.
    `prices_pence` has "offpeak", "day", "peak" (import) and "export_peak".
    Any KPI whose inputs are missing is None, never 0 (do-not-break 11).

    - Import bands: off-peak 02:00-05:00, peak 16:00-19:00, day = the rest
      (00:00-02:00, 05:00-16:00, 19:00-22:00). Morning = 05:00-16:00, the
      part of the day band an overnight shortfall shows up in (phase 15).
    - Avoidable import £ = day + peak import at their prices (what a perfect
      plan would have shifted to off-peak).
    - Unused charge = overnight SOC added that was still spare at 16:00 above
      the evening need (reserve + 16:00-22:00 load in hindsight + 22:00-02:00
      at `late_load_kw`), capped at what the night added.
    """
    imp = "grid_import_kwh"
    offpeak = _snap_delta(snapshots, "02:00", "05:00", imp)
    peak = _snap_delta(snapshots, "16:00", "19:00", imp)
    morning = _snap_delta(snapshots, "05:00", "16:00", imp)
    evening = _snap_delta(snapshots, "19:00", "22:00", imp)
    parts = (_snap(snapshots, "02:00", imp), morning, evening)
    day = None if any(p is None for p in parts) else sum(parts)

    load22, imp22 = _snap(snapshots, "22:00", "load_kwh"), _snap(snapshots, "22:00", imp)
    self_sufficiency = None
    if load22 is not None and imp22 is not None and load22 > 0:
        self_sufficiency = max(0.0, min(100.0, 100.0 * (1 - imp22 / load22)))

    avoidable_pence = None
    if day is not None and peak is not None:
        avoidable_pence = _mul(day, prices_pence.get("day"))
        peak_pence = _mul(peak, prices_pence.get("peak"))
        avoidable_pence = None if avoidable_pence is None or peak_pence is None else avoidable_pence + peak_pence

    export_peak = _snap_delta(snapshots, "16:00", "19:00", "grid_export_kwh")
    export_peak_pence = _mul(export_peak, prices_pence.get("export_peak"))

    unused = None
    soc02, soc05, soc16 = (_snap(snapshots, t, "soc") for t in ("02:00", "05:00", "16:00"))
    evening_load = _snap_delta(snapshots, "16:00", "22:00", "load_kwh")
    if None not in (soc02, soc05, soc16, evening_load, late_load_kw) and battery_capacity_kwh > 0:
        added_kwh = max(0.0, soc05 - soc02) / 100 * battery_capacity_kwh
        need_soc = KPI_RESERVE_SOC + (evening_load + late_load_kw * KPI_LATE_LOAD_HOURS) / battery_capacity_kwh * 100
        spare_kwh = max(0.0, soc16 - need_soc) / 100 * battery_capacity_kwh
        unused = min(added_kwh, spare_kwh)

    return {
        "grid_import_offpeak_kwh": round_or_none(offpeak),
        "grid_import_day_kwh": round_or_none(day),
        "grid_import_morning_kwh": round_or_none(morning),
        "grid_import_peak_kwh": round_or_none(peak),
        "grid_import_evening_kwh": round_or_none(evening),
        "self_sufficiency_pct": round_or_none(self_sufficiency, 1),
        "avoidable_import_gbp": round_or_none(None if avoidable_pence is None else avoidable_pence / 100),
        "export_peak_kwh": round_or_none(export_peak),
        "export_peak_gbp": round_or_none(None if export_peak_pence is None else export_peak_pence / 100),
        "unused_charge_kwh": round_or_none(unused),
    }


def week_kpis(days: list[dict[str, Any]]) -> dict[str, float | None]:
    """Weekly roll-up: kWh/£ fields summed, self-sufficiency averaged over days that have it."""
    out = {f"week_{key}": sum_field(days, key) for key in DAY_KPI_FIELDS if key != "self_sufficiency_pct"}
    ss = [d["self_sufficiency_pct"] for d in days if d.get("self_sufficiency_pct") is not None]
    out["week_self_sufficiency_pct"] = round(sum(ss) / len(ss), 1) if ss else None
    out["week_kpi_days"] = len(ss)
    return out


# Phase 15: overnight-target feedback from real day-rate import (shadow first).
IMPORT_FEEDBACK_WINDOW_DAYS = 14
IMPORT_FEEDBACK_MIN_DAYS = 5
IMPORT_FEEDBACK_TOLERANCE_KWH = 0.3
IMPORT_FEEDBACK_UNUSED_KWH = 0.5
IMPORT_FEEDBACK_STEP = 5


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def import_feedback_adjustment(
    paired_days: list[dict[str, Any]], today: date, band: str, away: bool
) -> tuple[int, int, str]:
    """Return (adjustment %, days used, reason) for tonight's target.

    Uses the last 14 days in tonight's forecast band and occupancy regime,
    excluding full-charge days, export-disabled days and nights already at
    100% (more charge wasn't possible). Needs 5 days.

    - Median 05:00-16:00 grid import above 0.3 kWh → +5 (the night ran short
      and the gap was bought at the day rate).
    - Otherwise, median unused overnight charge above 0.5 kWh → -5 (charge
      was still spare at 16:00).
    - Otherwise 0.
    """
    cutoff = (today - timedelta(days=IMPORT_FEEDBACK_WINDOW_DAYS)).isoformat()
    relevant = [
        d for d in paired_days
        if cutoff <= str(d.get("date", "")) < today.isoformat()
        and d.get("forecast_band") == band
        and not d.get("is_full_day")
        and not d.get("evening_export_disabled")
        and bool(d.get("away", False)) == away
        and (d.get("target_soc") or 0) < 100
        and d.get("grid_import_morning_kwh") is not None
    ]
    if len(relevant) < IMPORT_FEEDBACK_MIN_DAYS:
        return 0, len(relevant), "insufficient_days"
    if _median([d["grid_import_morning_kwh"] for d in relevant]) > IMPORT_FEEDBACK_TOLERANCE_KWH:
        return IMPORT_FEEDBACK_STEP, len(relevant), "day_rate_import"
    unused = [d["unused_charge_kwh"] for d in relevant if d.get("unused_charge_kwh") is not None]
    if unused and _median(unused) > IMPORT_FEEDBACK_UNUSED_KWH:
        return -IMPORT_FEEDBACK_STEP, len(relevant), "unused_charge"
    return 0, len(relevant), "on_target"


# Phase 16: keep enough at 19:00 to run the house to the 02:00 off-peak start.
EVENING_RESERVE_HOURS = 7.0
DEFAULT_TRIM_TARGET_SOC = 82


def evening_reserve_soc(
    load_kw: float, battery_capacity_kwh: float,
    hours: float = EVENING_RESERVE_HOURS, floor_soc: int = KPI_RESERVE_SOC,
) -> int:
    """SOC% needed at 19:00 to cover `hours` of load above the floor, capped at 100."""
    return min(100, math.ceil(floor_soc + load_kw * hours / battery_capacity_kwh * 100))


def trim_target_soc(soc: float, reserve_soc: int | None, default: int = DEFAULT_TRIM_TARGET_SOC) -> int | None:
    """Sell-down target floored at the evening reserve; None when that leaves nothing to trim."""
    target = max(default, reserve_soc or 0)
    return target if target < soc else None


def net_cost_gbp(import_cost: float | None, export_income: float | None, gas_cost: float | None) -> float | None:
    """import - export + gas, or None if any input is missing (never treat missing as £0)."""
    if import_cost is None or export_income is None or gas_cost is None:
        return None
    return round(import_cost - export_income + gas_cost, 2)


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


# Paired-day history to load for a trailing week: the oldest day's 01:55
# import_plan is ~7 d 16 h old at the Sunday 18:00 digest, and
# load_paired_days filters by recorded_at, so 7 would drop it.
WEEK_HISTORY_DAYS = 9


def trailing_week(today: date) -> tuple[str, str]:
    """ISO (start, end) of the 7 complete days ending yesterday.

    Today is excluded because its day_actuals isn't logged until 22:00.
    """
    return (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()


def days_in_period(days: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    """Paired days whose ISO `date` falls within [start, end]."""
    return [d for d in days if start <= str(d.get("date", "")) <= end]


def sum_field(days: list[dict[str, Any]], key: str) -> float | None:
    """Sum `key` across days that have it, rounded to 2dp; None if no day does."""
    values = [d[key] for d in days if d.get(key) is not None]
    return round(sum(values), 2) if values else None


_COMPLETE_COST_FIELDS = ("actual_import_cost_gbp", "actual_export_income_gbp", "actual_gas_cost_gbp")


def latest_complete_cost_day(days: list[dict[str, Any]], since: str) -> dict[str, Any] | None:
    """Newest day on/after ISO `since` with import, export and gas all logged.

    Octopus settles each sensor on its own lag (import/gas often a day behind
    export), so the newest logged day is usually partial. Returns a
    `daily_cost_complete` record with its net cost, or None if no day in the
    window is complete. Gas is required, matching net_cost_gbp.
    """
    complete = [
        d for d in days
        if str(d.get("date", "")) >= since and all(d.get(f) is not None for f in _COMPLETE_COST_FIELDS)
    ]
    if not complete:
        return None
    day = max(complete, key=lambda d: str(d["date"]))
    return {
        "type": "daily_cost_complete",
        "date": day["date"],
        **{f: day[f] for f in _COMPLETE_COST_FIELDS},
        "net_cost_gbp": net_cost_gbp(*(day[f] for f in _COMPLETE_COST_FIELDS)),
    }


# Never sell the battery below this SOC ahead of a free electricity event,
# even if the refillable-floor maths would allow it.
FREE_EVENT_MIN_FLOOR_SOC = 40


@dataclass
class FreeEventPlan:
    """Outcome of plan_free_event — see phases/10-free-electricity-event.md."""

    sell_start: datetime
    sell_end: datetime
    sell_floor_soc: int
    expected_export_kwh: float
    expected_refill_kwh: float
    skip_sell: bool


def plan_free_event(
    free_start: datetime,
    free_end: datetime,
    soc: float,
    capacity_kwh: float,
    charge_rate_kw: float,
    export_rate_kw: float,
) -> FreeEventPlan:
    """Plan the sell-before / refill-during shape of a free electricity event.

    Sells down to a "refillable floor" — the SOC that free-period charging at
    charge_rate_kw can top back up to 100% — never below FREE_EVENT_MIN_FLOOR_SOC.
    The sell window ends at free_start and is only as long as needed to reach the
    floor at export_rate_kw, capped at the free period's own length. If SOC is
    already at or below the floor, the sell is skipped (skip_sell=True) but the
    free-period import side still happens elsewhere.
    """
    free_hours = (free_end - free_start).total_seconds() / 3600
    refill_kwh = charge_rate_kw * free_hours
    raw_floor = 100 - (refill_kwh / capacity_kwh * 100 if capacity_kwh > 0 else 0)
    sell_floor_soc = int(max(FREE_EVENT_MIN_FLOOR_SOC, min(100, round(raw_floor))))

    skip_sell = soc <= sell_floor_soc
    sell_hours = 0.0
    expected_export_kwh = 0.0
    if not skip_sell:
        energy_to_sell_kwh = (soc - sell_floor_soc) / 100 * capacity_kwh
        if export_rate_kw > 0:
            sell_hours = min(energy_to_sell_kwh / export_rate_kw, free_hours)
            expected_export_kwh = round(sell_hours * export_rate_kw, 2)

    soc_at_free_start = soc - (expected_export_kwh / capacity_kwh * 100 if capacity_kwh > 0 else 0)
    headroom_kwh = max(0.0, capacity_kwh - soc_at_free_start / 100 * capacity_kwh)
    expected_refill_kwh = round(min(refill_kwh, headroom_kwh), 2)

    sell_start = free_start - timedelta(hours=sell_hours)

    return FreeEventPlan(
        sell_start=sell_start,
        sell_end=free_start,
        sell_floor_soc=sell_floor_soc,
        expected_export_kwh=expected_export_kwh,
        expected_refill_kwh=expected_refill_kwh,
        skip_sell=skip_sell,
    )


def manual_free_event_error(changed: str, start: datetime, end: datetime, now: datetime) -> str | None:
    """Validate a manual free-event pair after one field ('start' or 'end') was just set.

    The two fields are entered one at a time, so the field not being edited may
    still hold a stale value. Only the just-set field is judged: a start in the
    past is always an error, but a start at/after a stale end is silently
    accepted (the end is about to be set); an end at/before start is an error.
    Returns the message to notify, or None when the pair is fine or incomplete.
    """
    if changed == "start":
        if start <= now:
            return "Free event start must be in the future."
        return None
    if end <= start:
        return "Free event end must be after start."
    return None
