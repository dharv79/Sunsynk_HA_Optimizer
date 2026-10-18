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

import json
import math
import re
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


# Battery limits: the inverter stops discharging at the bottom and starts slowing
# discharge at the low mark, so plans size above the bottom and aim to stay above low.
BATTERY_BOTTOM_SOC = 20
BATTERY_LOW_SOC = 25


def bridge_soc(energy_kwh: float, battery_capacity_kwh: float) -> int:
    """SOC% needed to cover `energy_kwh` of load on top of the battery bottom, clamped 30-100."""
    return max(30, min(100, int(BATTERY_BOTTOM_SOC + energy_kwh / battery_capacity_kwh * 100)))


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
    """Add the drain buffer and evening nudge, keeping the estimated 06:00 SOC above the low mark."""
    target = max(BATTERY_BOTTOM_SOC, min(100, target_soc + drain_adjustment + evening_adjustment))
    if drain_adjustment > 0 and target - drain_adjustment < BATTERY_LOW_SOC:
        target = min(100, BATTERY_LOW_SOC + drain_adjustment)
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


# Phase 23: gentler charging — the lowest grid-charge current that still
# reaches the target by the end of the 02:00-05:00 cheap window.
CHEAP_WINDOW_HOURS = 3.0
CHEAP_WINDOW_END = "05:00"
PEAK_START_MINUTES = 16 * 60  # Flux peak band 16:00-19:00
PEAK_END_MINUTES = 19 * 60
GENTLE_CHARGE_MARGIN = 1.2
GENTLE_CHARGE_MIN_CURRENT_A = 10
DEFAULT_BATTERY_VOLTAGE = 51.2


def gentle_charge_current_a(
    kwh_needed: float,
    window_hours: float,
    battery_voltage: float,
    max_current_a: float,
    margin: float = GENTLE_CHARGE_MARGIN,
    min_current_a: float = GENTLE_CHARGE_MIN_CURRENT_A,
) -> int | None:
    """Battery current (A, rounded up) that delivers kwh_needed over window_hours.

    `margin` covers in-window house load and calibration error (phase 22's
    watchdog covers a night that still runs slow). Clamped to
    [min_current_a, max_current_a]; None when there is nothing to charge or an
    input is unusable — the caller then leaves the inverter's current alone.
    """
    if kwh_needed <= 0 or window_hours <= 0 or battery_voltage <= 0 or max_current_a <= 0:
        return None
    amps = math.ceil(kwh_needed * 1000 * margin / (battery_voltage * window_hours))
    return int(max(min(min_current_a, max_current_a), min(max_current_a, amps)))


def gentle_current_lowers(gentle_a: int | None, inverter_a: float | None) -> bool:
    """Gentle charging may only ever lower the inverter's own grid-charge
    current, never raise it (the user's setting is the ceiling)."""
    return gentle_a is not None and inverter_a is not None and gentle_a < inverter_a


def max_charge_current_a(charge_rate_kw: float, battery_voltage: float) -> float:
    """Nameplate charge rate as a battery current (A)."""
    return charge_rate_kw * 1000 / battery_voltage if battery_voltage > 0 else 0.0


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_to_minutes(hhmm: str) -> int | None:
    """Minutes after midnight for "HH:MM" (or "HH:MM:SS"), or None when malformed."""
    try:
        hours, minutes = str(hhmm).split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except (ValueError, TypeError):
        return None


CHARGE_WATCHDOG_MINUTES = 2 * 60 + 20  # 20 min into the 02:00 import window
CHARGE_WATCHDOG_RECHECK_MINUTES = 15

# Phase 32: a failed overnight plan push is retried every 10 min while the
# retry still lands inside the cheap charge's useful start (01:30-02:40).
PLAN_RETRY_INTERVAL_MINUTES = 10
PLAN_RETRY_FROM_MINUTES = 1 * 60 + 30
PLAN_RETRY_UNTIL_MINUTES = 2 * 60 + 40
PLAN_RETRY_MAX_ATTEMPTS = 5


def plan_retry_due(now_minutes: int, attempts: int) -> bool:
    """Whether to arm another plan-push retry PLAN_RETRY_INTERVAL_MINUTES from now.

    ``attempts`` counts retries already made (0 after the first failed push).
    """
    next_minutes = now_minutes + PLAN_RETRY_INTERVAL_MINUTES
    return (
        attempts < PLAN_RETRY_MAX_ATTEMPTS
        and PLAN_RETRY_FROM_MINUTES <= next_minutes <= PLAN_RETRY_UNTIL_MINUTES
    )
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
    def _num(key: str, default: float) -> float:
        value = item.get(key)
        return float(default if value is None else value)

    cloud = _num("cloud_coverage", 50)
    rain = _num("precipitation_probability", 0)
    temp = _num("temperature", 15)
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
    return round(_median([r for r, _ in rates]), 3), len(rates)


def resolve_load_kw(config_kw: float, learned_kw: float | None) -> tuple[float, str]:
    """Return (rate used, source): learned, clamped to 0.5x-2x config, else config."""
    if learned_kw is None:
        return config_kw, "config"
    low, high = LEARNED_LOAD_CLAMP
    return round(max(config_kw * low, min(config_kw * high, learned_kw)), 3), "learned"


# Phase 14: cumulative daily-meter snapshots at the Flux band edges. The
# SolarSynkV3 daily totals reset at midnight, so 22:00 closes the KPI day
# (22:00-24:00 is not covered, same as day_actuals).
KPI_RESERVE_SOC = BATTERY_BOTTOM_SOC
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
    "battery_saving_gbp",  # phase 37
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
        and d.get("target_soc") is not None and d["target_soc"] < 100
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


TRIM_FROM_MINUTES = 5 * 60  # end of the cheap import window


def trim_target_soc(
    soc: float,
    reserve_soc: int | None,
    default: int = DEFAULT_TRIM_TARGET_SOC,
    *,
    minutes: int | None = None,
    plan_target_soc: int | None = None,
) -> int | None:
    """Sell-down target floored at the evening reserve; None when that leaves nothing to trim.

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
    target = max(floors)
    return target if target < soc else None


# Phase 17: sell the surplus above the evening reserve 16:00-19:00 (shadow first).
PEAK_EXPORT_MARGIN_SOC = 5
PEAK_EXPORT_MIN_KWH = 0.5  # below this it isn't worth an API write
ROUND_TRIP_EFFICIENCY = 0.85


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


def peak_export_skip_reason(slots_held: bool, soc: float | None) -> str | None:
    """Why the 16:00 peak export is skipped (logged as its decision), or None to plan it."""
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
) -> dict[str, Any]:
    """16:00 decision: export the SOC above reserve + margin at the peak rate.

    The kWh sold would otherwise reduce tomorrow's off-peak charge, so it is
    worth it only if the peak export price beats the off-peak replacement cost
    after round-trip losses. `decision` is export / no_surplus / not_worth_it /
    no_price; gain is export income minus replacement cost.
    """
    target = min(100, reserve_soc + margin_soc)
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
    }


# Phase 30: peak cover — make sure the battery reaches 16:00 with enough charge
# to run the house through 16:00-19:00, topping up at the (cheaper) day rate.
PEAK_COVER_FLOOR_SOC = BATTERY_LOW_SOC  # full-power discharge through the peak
PEAK_COVER_MARGIN = 1.1
PEAK_COVER_MIN_SHORTFALL_SOC = 3
PEAK_COVER_LEAD_MINUTES = 45  # start this long before the charge needs to begin
PEAK_COVER_FROM_MINUTES = 12 * 60
PEAK_COVER_PEAK_START_MINUTES = PEAK_START_MINUTES
PEAK_COVER_LAST_LOG_MINUTES = 15 * 60 + 30
PEAK_COVER_MIN_LOAD_HOURS = 2.0  # hours of 05:00-now data before the observed load is trusted
PEAK_COVER_LOAD_PERCENTILE = 75
PEAK_COVER_MIN_PEAK_DAYS = 3


def solar_until_kwh(hourly: dict[int, float] | None, now_hour: float, end_hour: float = 16.0) -> float | None:
    """Forecast solar (kWh) between now_hour and end_hour from {hour: kWh} buckets; None without a forecast."""
    if not hourly:
        return None
    total = 0.0
    for hour, kwh in hourly.items():
        overlap = min(end_hour, hour + 1) - max(now_hour, hour)
        if overlap > 0:
            total += kwh * overlap
    return round(total, 3)


def daytime_load_kw(
    load_now_kwh: float | None, load_at_05_kwh: float | None, hours: float, fallback_kw: float
) -> tuple[float, str]:
    """House load (kW) since 05:00 from the daily meter; the fallback until 2 h of data exist."""
    if load_now_kwh is None or load_at_05_kwh is None or hours < PEAK_COVER_MIN_LOAD_HOURS:
        return fallback_kw, "fallback"
    delta = load_now_kwh - load_at_05_kwh
    if delta <= 0:
        return fallback_kw, "fallback"
    return round(delta / hours, 3), "observed"


def peak_load_estimate_kwh(recent_kwh: list[float], default_kwh: float) -> tuple[float, str]:
    """Expected 16:00-19:00 house load: the 75th percentile of recent days (covers most days), else the default."""
    values = sorted(v for v in recent_kwh if v is not None and v >= 0)
    if len(values) < PEAK_COVER_MIN_PEAK_DAYS:
        return default_kwh, "default"
    rank = PEAK_COVER_LOAD_PERCENTILE / 100.0 * (len(values) - 1)
    low = int(rank)
    high = min(low + 1, len(values) - 1)
    return round(values[low] + (values[high] - values[low]) * (rank - low), 2), "history"


def peak_cover_worth_it(day_pence: float | None, peak_pence: float | None, efficiency: float = CHARGE_EFFICIENCY) -> bool | None:
    """True when charging at the day rate beats buying the same kWh at the peak rate after charge losses.
    None when a price is missing — never treat a missing price as 0p."""
    if day_pence is None or peak_pence is None or efficiency <= 0:
        return None
    return peak_pence > day_pence / efficiency


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
) -> dict[str, Any]:
    """Will the battery reach 16:00 with enough to run the peak window? If not, how to top up.

    Projects SOC at 16:00 from the current SOC, forecast solar and house load
    (a battery that would run flat sits at the floor, the grid covering the
    rest), against the SOC needed to carry the expected peak load above the
    floor. decision: ok / top_up / not_worth_it / no_price. A top-up is a Flux 1
    slot now->16:00 at target_soc: the inverter only imports while below the
    target, so it holds the battery there at the day rate. `push_now` waits
    until the charge would need to start (plus a lead) so later solar can still
    help. Gain is the peak cost avoided minus the day-rate cost of the top-up.
    """
    hours = max(0.0, minutes_to_peak / 60.0)
    projected = soc + (solar_kwh - load_kw * hours) / capacity_kwh * 100.0
    projected_soc = round(min(100.0, max(float(floor_soc), projected)), 1)
    required_soc = min(100, math.ceil(floor_soc + peak_load_kwh * margin / capacity_kwh * 100.0))
    shortfall_soc = max(0.0, required_soc - projected_soc)
    worth = peak_cover_worth_it(day_pence, peak_pence)
    if worth is None:
        decision = "no_price"
    elif shortfall_soc < PEAK_COVER_MIN_SHORTFALL_SOC:
        decision = "ok"
    elif not worth:
        decision = "not_worth_it"
    else:
        decision = "top_up"
    topping = decision == "top_up"
    topup_kwh = round(shortfall_soc / 100.0 * capacity_kwh, 2) if topping else 0.0
    charge_minutes = math.ceil(topup_kwh / CHARGE_EFFICIENCY / charge_rate_kw * 60.0) if topping and charge_rate_kw > 0 else 0
    gain_gbp = None
    if topping:
        gain_gbp = round_or_none(topup_kwh * (peak_pence - day_pence / CHARGE_EFFICIENCY) / 100.0)
    return {
        "decision": decision,
        "projected_soc_16": projected_soc,
        "required_soc": required_soc,
        "shortfall_soc": round(shortfall_soc, 1),
        "target_soc": required_soc if topping else None,
        "topup_kwh": topup_kwh,
        "charge_minutes": charge_minutes,
        "push_now": topping and minutes_to_peak <= charge_minutes + PEAK_COVER_LEAD_MINUTES,
        "gain_gbp": gain_gbp,
    }


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
# Longest sell window before a free period (16/10/2026: was the free period's
# own length, which left a full battery unable to take the free import).
FREE_EVENT_MAX_SELL_HOURS = 4.0
# The sell start is decided live: from FREE_EVENT_MAX_SELL_HOURS before the free
# start, every FREE_EVENT_WATCH_MINUTES, from the SOC and its recent trend.
FREE_EVENT_WATCH_MINUTES = 10
FREE_EVENT_TREND_MINUTES = 30  # SOC trend window (the battery's real charge rate)
FREE_EVENT_TREND_MIN_MINUTES = 5  # shorter spans are too noisy at 1% SOC steps
FREE_EVENT_SELL_MARGIN_HOURS = 0.25


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
    max_sell_hours: float = FREE_EVENT_MAX_SELL_HOURS,
) -> FreeEventPlan:
    """Plan the sell-before / refill-during shape of a free electricity event.

    Sells down to a "refillable floor" — the SOC that free-period charging at
    charge_rate_kw can top back up to 100% — never below FREE_EVENT_MIN_FLOOR_SOC.
    The sell window ends at free_start and is only as long as needed at
    export_rate_kw, capped at ``max_sell_hours``. This is the estimate shown
    when the event is scheduled; the real sell start is decided live on the day
    (free_event_sell_decision). Nothing to sell → skip_sell=True.
    """
    free_hours = (free_end - free_start).total_seconds() / 3600
    refill_kwh = charge_rate_kw * free_hours
    raw_floor = 100 - (refill_kwh / capacity_kwh * 100 if capacity_kwh > 0 else 0)
    sell_floor_soc = int(max(FREE_EVENT_MIN_FLOOR_SOC, min(100, round(raw_floor))))

    energy_to_sell_kwh = (soc - sell_floor_soc) / 100 * capacity_kwh
    skip_sell = energy_to_sell_kwh <= 0.05
    sell_hours = 0.0
    expected_export_kwh = 0.0
    if not skip_sell and export_rate_kw > 0:
        sell_hours = min(energy_to_sell_kwh / export_rate_kw, max_sell_hours)
        expected_export_kwh = round(sell_hours * export_rate_kw, 2)

    if capacity_kwh > 0:
        soc_at_free_start = min(100.0, max(0.0, soc - expected_export_kwh / capacity_kwh * 100))
    else:
        soc_at_free_start = soc
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


def free_event_watch_from(free_start: datetime) -> datetime:
    """When the live sell-start watch begins (the longest sell window before the free start)."""
    return free_start - timedelta(hours=FREE_EVENT_MAX_SELL_HOURS)


# Scheduling-time sell estimates; only meaningful once the live watch has started a sell.
FREE_EVENT_ESTIMATE_KEYS = ("sell_start", "sell_end", "skip_sell", "expected_export_kwh")


def free_event_sold(event: dict) -> bool:
    """True once the sell start has run (it always records ``soc_at_sell_start``)."""
    return "soc_at_sell_start" in event


def free_event_sell_status(event: dict) -> str | None:
    """Dashboard text for the sell: watching, selling, sold or not needed."""
    phase = event.get("phase")
    if not phase or not event.get("free_start"):
        return None
    if phase == "scheduled":
        watch_from = event.get("watch_from") or free_event_watch_from(
            datetime.fromisoformat(event["free_start"])
        ).isoformat()
        return f"Watching from {datetime.fromisoformat(watch_from):%a %H:%M}"
    if phase == "cancelled":
        return "Cancelled"
    if not free_event_sold(event) or not event.get("sell_start"):
        return "Not needed"
    sell_start = datetime.fromisoformat(event["sell_start"])
    return f"Selling since {sell_start:%H:%M}" if phase == "selling" else f"Sold from {sell_start:%H:%M}"


def free_event_attributes(event: dict) -> dict:
    """Sensor attributes: no raw meters, no sell estimates until a sell has run, plus ``sell_status``."""
    hidden = () if free_event_sold(event) else FREE_EVENT_ESTIMATE_KEYS
    attrs = {k: v for k, v in event.items() if not k.startswith("meters_") and k not in hidden}
    status = free_event_sell_status(event)
    if status:
        attrs["sell_status"] = status
    return attrs


def soc_trend_kw(samples: list[tuple[datetime, float]], capacity_kwh: float, now: datetime) -> float | None:
    """Battery charge rate (kW, negative = discharging) from SOC samples in the last
    FREE_EVENT_TREND_MINUTES; None until they span FREE_EVENT_TREND_MIN_MINUTES."""
    recent = [(ts, soc) for ts, soc in samples if (now - ts).total_seconds() <= FREE_EVENT_TREND_MINUTES * 60]
    if len(recent) < 2:
        return None
    (t0, s0), (t1, s1) = recent[0], recent[-1]
    hours = (t1 - t0).total_seconds() / 3600
    if hours * 60 < FREE_EVENT_TREND_MIN_MINUTES or capacity_kwh <= 0:
        return None
    return round((s1 - s0) / 100 * capacity_kwh / hours, 2)


def free_event_sell_decision(
    soc: float,
    floor_soc: float,
    capacity_kwh: float,
    trend_kw: float | None,
    export_rate_kw: float,
    hours_left: float,
    margin_hours: float = FREE_EVENT_SELL_MARGIN_HOURS,
) -> dict[str, Any]:
    """Whether to start selling now ahead of a free period.

    Projects the SOC at the free start from the measured charge trend (solar
    going into the battery, capped at 100%; a falling trend is ignored), then
    the hours needed to export down to the floor. Start once that, plus a
    margin, reaches the time left.
    """
    charge_kw = max(0.0, trend_kw or 0.0)
    projected_soc = min(100.0, soc + (charge_kw * hours_left / capacity_kwh * 100 if capacity_kwh > 0 else 0.0))
    energy_kwh = max(0.0, (projected_soc - floor_soc) / 100 * capacity_kwh)
    needed_hours = energy_kwh / export_rate_kw if export_rate_kw > 0 else None
    sell_now = (
        energy_kwh > 0.05 and needed_hours is not None and needed_hours + margin_hours >= hours_left
    )
    return {
        "sell_now": sell_now,
        "trend_kw": trend_kw,
        "projected_soc": round(projected_soc, 1),
        "energy_kwh": round(energy_kwh, 2),
        "needed_hours": round(needed_hours, 2) if needed_hours is not None else None,
        "hours_left": round(hours_left, 2),
    }


def free_event_target_cap(
    target_soc: int,
    event: dict[str, Any] | None,
    free_start: datetime | None,
    plan_date: date,
    load_kw: float | None = None,
    capacity_kwh: float | None = None,
) -> int:
    """01:55 target on a free-event day: the sell floor plus the house load until the free start.

    The floor is where the battery should be at the free start, not at 05:00:
    the house runs on it from the end of the cheap window until then, so that
    load is added (no solar credit; any surplus is sold by the live watch).
    Charging further would only be sold back at a lower export price. Applies
    when the event (still scheduled) starts on the plan date before the 16:00
    peak; otherwise unchanged.
    """
    event = event or {}
    floor = event.get("sell_floor_soc")
    if (
        event.get("phase") != "scheduled" or not _is_number(floor) or free_start is None
        or free_start.date() != plan_date or free_start.hour * 60 + free_start.minute >= PEAK_START_MINUTES
    ):
        return target_soc
    cap = int(floor)
    if _is_number(load_kw) and _is_number(capacity_kwh) and capacity_kwh > 0:
        hh, mm = CHEAP_WINDOW_END.split(":")
        cheap_end = int(hh) * 60 + int(mm)
        bridge_hours = max(0, free_start.hour * 60 + free_start.minute - cheap_end) / 60
        cap += math.ceil(max(0.0, load_kw) * bridge_hours / capacity_kwh * 100)
    return min(target_soc, min(100, cap))


def manual_free_event_error(
    changed: str, start: datetime, end: datetime, now: datetime, label: str = "Free event"
) -> str | None:
    """Validate a manual event pair after one field ('start' or 'end') was just set.

    The two fields are entered one at a time, so the field not being edited may
    still hold a stale value. Only the just-set field is judged: a start in the
    past is always an error, but a start at/after a stale end is silently
    accepted (the end is about to be set); an end at/before start is an error.
    Returns the message to notify, or None when the pair is fine or incomplete.
    `label` names the event in the message.
    """
    if changed == "start":
        if start <= now:
            return f"{label} start must be in the future."
        return None
    if end <= start:
        return f"{label} end must be after start."
    return None


def _as_aware(value: Any, tz) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


SPARE_SOLAR_FIRST_HOUR = 8
SPARE_SOLAR_WINDOW_HOURS = 3


def expected_spare_solar(
    hourly_forecast_kwh: dict[int, float] | None,
    correction: float,
    load_kw: float,
    battery_headroom_kwh: float,
    from_hour: float = SPARE_SOLAR_FIRST_HOUR,
    to_hour: float = 16.0,
) -> dict:
    """Solar left over for flexible loads (phase 25).

    Per hour: corrected solar minus house load; the battery refills first from
    the earliest surplus (headroom), and what remains is spare. Returns
    {"total_kwh", "hours": [{"hour", "spare_kwh"}], "window": {"start", "end",
    "kwh"} | None} where the window is the best 3-hour run. No forecast -> None
    total (never 0).
    """
    if not hourly_forecast_kwh:
        return {"total_kwh": None, "hours": [], "window": None}
    remaining = max(0.0, battery_headroom_kwh)
    hours = []
    for hour in sorted(hourly_forecast_kwh):
        overlap = min(to_hour, hour + 1) - max(from_hour, hour)
        if overlap <= 0:
            continue
        surplus = max(0.0, hourly_forecast_kwh[hour] * correction * overlap - max(0.0, load_kw) * overlap)
        refill = min(surplus, remaining)
        remaining -= refill
        hours.append({"hour": hour, "spare_kwh": round(surplus - refill, 2)})
    total = round(sum(h["spare_kwh"] for h in hours), 2)
    best: list[dict] = []
    best_kwh = 0.0
    for i in range(len(hours)):
        run = [h for h in hours[i : i + SPARE_SOLAR_WINDOW_HOURS] if h["hour"] < hours[i]["hour"] + SPARE_SOLAR_WINDOW_HOURS]
        kwh = sum(h["spare_kwh"] for h in run)
        if kwh > best_kwh:
            best, best_kwh = run, kwh
    while best and best[0]["spare_kwh"] <= 0:
        best = best[1:]
    while best and best[-1]["spare_kwh"] <= 0:
        best = best[:-1]
    window = (
        {"start": f"{best[0]['hour']:02d}:00", "end": f"{best[-1]['hour'] + 1:02d}:00",
         "kwh": round(sum(h["spare_kwh"] for h in best), 2)}
        if best else None
    )
    return {"total_kwh": total, "hours": hours, "window": window}


BASELOAD_MIN_NIGHTS = 7
BASELOAD_FACTOR = 1.5
BASELOAD_MIN_EXTRA_KWH = 1.0


def baseload_anomaly(
    history: list[dict[str, Any]],
    tonight_kwh: float | None,
    min_nights: int = BASELOAD_MIN_NIGHTS,
    factor: float = BASELOAD_FACTOR,
    min_extra_kwh: float = BASELOAD_MIN_EXTRA_KWH,
) -> dict[str, Any] | None:
    """Return {"tonight_kwh", "usual_kwh", "nights"} when 00:00-05:00 load is abnormally high, else None.

    ``history`` rows are ``{"load_kwh", "away"}``. Home nights only (away is a
    different regime, do-not-break 18); missing or non-positive readings are
    ignored, never counted as 0 (do-not-break 11). Needs ``min_nights`` of
    history, and tonight must beat the median by both ``factor`` and
    ``min_extra_kwh`` so a small base can't alert on noise.
    """
    if not isinstance(tonight_kwh, (int, float)) or tonight_kwh <= 0:
        return None
    values = sorted(
        float(r["load_kwh"])
        for r in history
        if not r.get("away") and isinstance(r.get("load_kwh"), (int, float)) and r["load_kwh"] > 0
    )
    if len(values) < min_nights:
        return None
    usual = _median(values)
    if tonight_kwh >= usual * factor and tonight_kwh - usual >= min_extra_kwh:
        return {"tonight_kwh": round(tonight_kwh, 2), "usual_kwh": round(usual, 2), "nights": len(values)}
    return None


# Daytime load spike alert (phase 40): day-rate grid import while the battery
# should be covering the house.
DAYTIME_IMPORT_THRESHOLD_W = 1500.0
DAYTIME_IMPORT_AWAY_THRESHOLD_W = 750.0
DAYTIME_IMPORT_CHECKS = 2
DAYTIME_IMPORT_SOC_MARGIN = 5
DAYTIME_IMPORT_FROM_HOUR = 5
DAYTIME_IMPORT_UNTIL_HOUR = 16


def daytime_import_anomaly(
    samples: list[float | None],
    soc: float | None,
    threshold_w: float = DAYTIME_IMPORT_THRESHOLD_W,
    checks: int = DAYTIME_IMPORT_CHECKS,
) -> dict[str, Any] | None:
    """Return {"grid_w", "soc", "checks"} when grid import stayed above ``threshold_w``, else None.

    ``samples`` are the most recent grid_pac readings (W, import positive),
    oldest first. The last ``checks`` must all be present and above the
    threshold, and SOC must be above ``BATTERY_LOW_SOC`` + margin (below that
    the inverter imports by design). Missing readings never alert.
    """
    if not isinstance(soc, (int, float)) or soc <= BATTERY_LOW_SOC + DAYTIME_IMPORT_SOC_MARGIN:
        return None
    recent = samples[-checks:]
    if len(recent) < checks or not all(isinstance(s, (int, float)) and s > threshold_w for s in recent):
        return None
    return {"grid_w": round(min(recent)), "soc": round(soc), "checks": checks}


DEFAULT_AUTO_AWAY_MIN_HOURS = 24.0


def auto_away(
    persons_home: list[bool | None],
    calendar_away: bool | None,
    min_hours_away: float = DEFAULT_AUTO_AWAY_MIN_HOURS,
    persons_away_hours: list[float | None] | None = None,
) -> bool | None:
    """Decide away mode from presence and calendar signals (phase 27).

    ``persons_home`` is one entry per configured person (True home, False away,
    None unknown/unavailable); ``persons_away_hours`` is how long each has been
    away. Returns True (away), False (home) or None (no usable signal, or not
    away long enough yet) — None means leave the manual switch alone.
    Anyone home wins immediately; away needs every known person away for
    ``min_hours_away`` or an active calendar event.
    """
    known = [(h, i) for i, h in enumerate(persons_home) if h is not None]
    if any(h for h, _ in known):
        return False
    if calendar_away:
        return True
    if known:
        hours = persons_away_hours or []
        if all(i < len(hours) and isinstance(hours[i], (int, float)) and hours[i] >= min_hours_away for _, i in known):
            return True
        return None
    if calendar_away is False:
        return False
    return None


# Phase 29: clipped solar (battery full, export pinned at the inverter limit).
CLIPPED_SOC_PCT = 99.0
CLIPPED_EXPORT_FRACTION = 0.95
CLIPPED_EARLY_FULL_HOUR = 11


def is_clipping(soc: float | None, grid_pac_w: float | None, export_limit_w: float | None) -> bool:
    """True when the battery is full and export sits at ≥95% of the limit (grid_pac < 0 is export).

    Any missing input or a non-positive limit (feature off) is False, never a guess.
    """
    if soc is None or grid_pac_w is None or not export_limit_w or export_limit_w <= 0:
        return False
    return soc >= CLIPPED_SOC_PCT and -grid_pac_w >= CLIPPED_EXPORT_FRACTION * export_limit_w


def estimate_clipped_kwh(
    forecast_hour_kwh: float | None, actual_pv_w: float | None, period_hours: float = 0.5
) -> float | None:
    """Solar lost over one clipped period: forecast minus actual PV, floored at 0.

    ``forecast_hour_kwh`` is the forecast for the current hour (kWh); None when
    the forecast or PV reading is missing — never counted as 0 (rules 11/14).
    """
    if forecast_hour_kwh is None or actual_pv_w is None or period_hours <= 0:
        return None
    return round(max(0.0, forecast_hour_kwh * period_hours - actual_pv_w / 1000.0 * period_hours), 3)


def overnight_target_too_high(clipped_kwh: float | None, first_full_hour: float | None) -> bool | None:
    """Flag a day that clipped after the battery was already full before 11:00 (target too high).

    None when there is nothing to judge (no clipping data or never seen full).
    """
    if clipped_kwh is None or first_full_hour is None:
        return None
    return clipped_kwh > 0 and first_full_hour < CLIPPED_EARLY_FULL_HOUR


# --- Tariff comparison (phase 28) -------------------------------------------

# Approximate public unit rates in pence/kWh, last checked 13/10/2026. They drift
# with Ofgem's cap, so users can override any rate via the options JSON.
TARIFF_RATES_AS_OF = "13/10/2026"
# "standing" is p/day: Octopus charges the same regional standing charge on its
# variable tariffs, defaulted from the user's Flux bills (54.62p, 14/10/2026).
# Unit rates are ex-VAT; VAT is applied once in compare_tariffs.
TARIFF_STANDING_P_PER_DAY = 54.62
TARIFF_DEFAULT_RATES: dict[str, dict[str, float]] = {
    "flux": {"standing": TARIFF_STANDING_P_PER_DAY},  # unit rates come from the user's charges
    "go": {"cheap": 8.5, "day": 28.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "cosy": {"cheap": 13.5, "peak": 40.5, "day": 27.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "intelligent": {"cheap": 7.0, "day": 28.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "agile": {"export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},  # import priced per slot
}
# Domestic VAT on the user's bills is 0% from 01/10/2026; override with "vat_percent".
TARIFF_VAT_PERCENT = 0.0
# (start "HH:MM", end "HH:MM", rate key); an end before its start wraps midnight.
TARIFF_IMPORT_BANDS: dict[str, list[tuple[str, str, str]]] = {
    "go": [("00:30", "05:30", "cheap"), ("05:30", "00:30", "day")],
    "cosy": [
        ("04:00", "07:00", "cheap"), ("13:00", "16:00", "cheap"),
        ("16:00", "19:00", "peak"), ("07:00", "13:00", "day"), ("19:00", "04:00", "day"),
    ],
    # Intelligent Octopus Go: the fixed cheap window only (smart-charge extras out of scope).
    "intelligent": [("23:30", "05:30", "cheap"), ("05:30", "23:30", "day")],
}
TARIFF_LABELS = {
    "flux": "Flux", "go": "Octopus Go", "cosy": "Cosy Octopus",
    "intelligent": "Intelligent Octopus Go", "agile": "Agile Octopus",
}
HALF_HOURS_PER_DAY = 48


def band_price(bands: list[tuple[int, int, float]], minute: int) -> float | None:
    """Price (p/kWh) of the band covering ``minute`` of the day; None when no band does."""
    for start, end, price in bands:
        inside = start <= minute < end if start < end else (minute >= start or minute < end)
        if inside:
            return price
    return None


def bands_from_rates(
    spec: list[tuple[str, str, str]], rates: dict[str, float]
) -> list[tuple[int, int, float]]:
    """Resolve a (start, end, rate key) spec into minute bands; bands with a bad key or time are dropped."""
    out: list[tuple[int, int, float]] = []
    for start, end, key in spec:
        s, e, price = hhmm_to_minutes(start), hhmm_to_minutes(end), rates.get(key)
        if s is not None and e is not None and isinstance(price, (int, float)):
            out.append((s, e, float(price)))
    return out


def flux_bands_from_charges(charges: Any, status: str) -> list[tuple[int, int, float]]:
    """The user's configured Flux bands for ``status`` ("import"/"export") as minute bands."""
    out: list[tuple[int, int, float]] = []
    for row in charges if isinstance(charges, list) else []:
        if not isinstance(row, dict) or row.get("status") != status:
            continue
        s, e = hhmm_to_minutes(row.get("startRange")), hhmm_to_minutes(row.get("endRange"))
        price = row.get("price")
        if s is not None and e is not None and isinstance(price, (int, float)):
            out.append((s, e, float(price)))
    return out


def _overrides_dict(overrides: Any) -> dict[str, Any]:
    """The options' tariff overrides JSON as a dict; junk → {}."""
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides) if overrides.strip() else {}
        except ValueError:
            overrides = {}
    return overrides if isinstance(overrides, dict) else {}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def tariff_vat_percent(overrides: Any) -> float:
    """VAT % for the comparison: the "vat_percent" override when valid (0-100), else the dated default."""
    vat = _overrides_dict(overrides).get("vat_percent")
    return float(vat) if _is_number(vat) and 0 <= vat <= 100 else TARIFF_VAT_PERCENT


def agile_region_override(overrides: Any) -> str | None:
    """The "agile_region" override (a DNO region letter A-P), else None."""
    region = _overrides_dict(overrides).get("agile_region")
    return region.upper() if isinstance(region, str) and re.fullmatch(r"[A-Pa-p]", region) else None


def octopus_region(tariff_code: Any) -> str | None:
    """Region letter from an Octopus tariff code ("E-1R-FLUX-IMPORT-23-02-14-C" → "C")."""
    match = re.fullmatch(r"E-[12]R-.+-([A-P])", str(tariff_code or ""))
    return match.group(1) if match else None


def agile_prices_by_date(results: Any, tz: Any = None) -> dict[str, list[float]]:
    """{local date: 48 ex-VAT p/kWh} from Octopus standard-unit-rates results.

    Only complete days are kept (DST days with 46/50 slots and partial days
    are dropped rather than guessed, matching parse_half_hourly_charges).
    """
    slots: dict[str, dict[int, float]] = {}
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict) or not _is_number(row.get("value_exc_vat")):
            continue
        start = _as_aware(row.get("valid_from"), tz)
        if start is None:
            continue
        local = start.astimezone(tz) if tz is not None else start
        slots.setdefault(local.date().isoformat(), {})[local.hour * 2 + local.minute // 30] = float(
            row["value_exc_vat"]
        )
    return {
        day: [by_idx[i] for i in range(HALF_HOURS_PER_DAY)]
        for day, by_idx in slots.items()
        if len(by_idx) == HALF_HOURS_PER_DAY
    }


def newest_agile_product(products: list[dict[str, Any]], before: datetime) -> str | None:
    """Code of the newest Agile import product that was on sale by ``before``."""
    best: tuple[str, str] | None = None
    for product in products:
        code, available = str(product.get("code", "")), str(product.get("available_from") or "")
        if not code.startswith("AGILE-") or "OUTGOING" in code or product.get("direction") not in (None, "IMPORT"):
            continue
        if available and available[:10] > before.date().isoformat():
            continue
        if best is None or available > best[1]:
            best = (code, available)
    return best[0] if best else None


def merge_tariff_rates(overrides: Any) -> dict[str, dict[str, float]]:
    """Default rates with user overrides applied; unknown tariffs/keys and non-numbers are ignored."""
    rates = {name: dict(r) for name, r in TARIFF_DEFAULT_RATES.items()}
    for name, vals in _overrides_dict(overrides).items():
        if name in rates and isinstance(vals, dict):
            for key, val in vals.items():
                if key in rates[name] and isinstance(val, (int, float)) and not isinstance(val, bool):
                    rates[name][key] = float(val)
    return rates


def parse_half_hourly_charges(attrs: Any) -> tuple[str, list[float]] | None:
    """(date, 48 kWh values by half-hour of the local day) from an Octopus ``charges`` attribute.

    None unless the day is complete: exactly 48 distinct slots on one date, all numeric
    (DST days with 46/50 slots are skipped rather than guessed — rule 11).
    """
    charges = attrs.get("charges") if isinstance(attrs, dict) else None
    if not isinstance(charges, list) or len(charges) != HALF_HOURS_PER_DAY:
        return None
    slots: list[float | None] = [None] * HALF_HOURS_PER_DAY
    day: str | None = None
    for row in charges:
        try:
            start = datetime.fromisoformat(str(row["start"]))
            kwh = row["consumption"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(kwh, (int, float)) or isinstance(kwh, bool):
            return None
        if day is None:
            day = start.date().isoformat()
        elif start.date().isoformat() != day:
            return None
        idx = start.hour * 2 + start.minute // 30
        if slots[idx] is not None:
            return None
        slots[idx] = float(kwh)
    if day is None or any(v is None for v in slots):
        return None
    return day, [v for v in slots if v is not None]


def cost_under_tariff(half_hourly_kwh: Any, bands: list[tuple[int, int, float]] | list[float]) -> float | None:
    """Pence for a day of 48 half-hourly kWh; None if any slot is missing or unpriced.

    ``bands`` is either minute bands (start, end, p) or 48 per-slot prices (Agile).
    """
    if not isinstance(half_hourly_kwh, (list, tuple)) or len(half_hourly_kwh) != HALF_HOURS_PER_DAY:
        return None
    per_slot = bool(bands) and all(_is_number(b) for b in bands)
    if per_slot and len(bands) != HALF_HOURS_PER_DAY:
        return None
    total = 0.0
    for idx, kwh in enumerate(half_hourly_kwh):
        price = float(bands[idx]) if per_slot else band_price(bands, idx * 30)
        if price is None or not isinstance(kwh, (int, float)) or isinstance(kwh, bool):
            return None
        total += kwh * price
    return round(total, 2)


def compare_tariffs(
    import_by_date: dict[str, list[float]],
    export_by_date: dict[str, list[float]],
    tariffs: dict[str, tuple[Any, Any]],
    month: str,
    standing: dict[str, float] | None = None,
    vat_percent: float = 0.0,
) -> dict[str, Any]:
    """Per-tariff £ over the days of ``month`` ("YYYY-MM"), usage held fixed.

    Each tariff is (import, export) pricing: minute bands, or for Agile a
    {date: 48 slot prices} dict. ``net_gbp`` is unit rates only (import cost
    minus export income, kept for continuity); ``total_gbp`` adds the standing
    charge (p/day) and VAT on import + standing — closer to a bill. Only days
    with both import and export logged count; a tariff that can't price some
    counted day (or has no standing charge) gets None, never a partial total.
    """
    days = sorted(d for d in import_by_date if d.startswith(month) and d in export_by_date)
    standing = standing or {}
    net: dict[str, float | None] = {}
    total: dict[str, float | None] = {}
    for name, (imp_spec, exp_spec) in tariffs.items():
        imp_p: float | None = 0.0 if days else None
        exp_p = 0.0
        for d in days:
            imp_prices = imp_spec.get(d) if isinstance(imp_spec, dict) else imp_spec
            exp_prices = exp_spec.get(d) if isinstance(exp_spec, dict) else exp_spec
            imp = cost_under_tariff(import_by_date[d], imp_prices) if imp_prices else None
            exp = cost_under_tariff(export_by_date[d], exp_prices) if exp_prices else None
            if imp is None or exp is None:
                imp_p = None
                break
            imp_p += imp
            exp_p += exp
        net[name] = None if imp_p is None else round((imp_p - exp_p) / 100.0, 2)
        day_charge = standing.get(name)
        total[name] = (
            None
            if imp_p is None or not _is_number(day_charge)
            else round(((imp_p + day_charge * len(days)) * (1 + vat_percent / 100.0) - exp_p) / 100.0, 2)
        )
    ranked = total if any(v is not None for v in total.values()) else net
    priced = {k: v for k, v in ranked.items() if v is not None}
    cheapest = min(priced, key=lambda k: priced[k]) if priced else None
    return {
        "month": month,
        "days_compared": len(days),
        "days_missing": sum(1 for d in import_by_date if d.startswith(month) and d not in export_by_date)
        + sum(1 for d in export_by_date if d.startswith(month) and d not in import_by_date),
        "net_gbp": net,
        "total_gbp": total,
        "standing_p_per_day": {k: standing.get(k) for k in tariffs},
        "vat_percent": vat_percent,
        "cheapest": cheapest,
        "rates_as_of": TARIFF_RATES_AS_OF,
    }


# Flux 2 slots shared by the plan, the export pause and the post-event restore.
DEFAULT_FLUX2_SLOT = {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85}
EXPORT_PAUSE_FLUX2_SLOT = {"startTime": "16:00", "endTime": "16:15", "targetSoc": 100}


def restore_flux2_slot(export_paused: bool, minutes: int) -> dict[str, Any]:
    """Flux 2 slot to put back after an event: keeps an active export pause inside 16:00-19:00.

    Restoring the default slot there would drop the pause while state still says
    paused, so the Flux 2 check (idempotent on "already disabled") never re-pushes it.
    """
    paused = export_paused and PEAK_START_MINUTES <= minutes < PEAK_END_MINUTES
    return dict(EXPORT_PAUSE_FLUX2_SLOT if paused else DEFAULT_FLUX2_SLOT)


def meter_delta(start: dict[str, Any] | None, end: dict[str, Any] | None, key: str) -> float | None:
    """kWh between two daily-meter readings; None when either is missing or the meter reset (midnight)."""
    a, b = (start or {}).get(key), (end or {}).get(key)
    if not _is_number(a) or not _is_number(b) or b < a:
        return None
    return round(b - a, 2)


def _gbp(kwh: float | None, pence: float | None) -> float | None:
    return round(kwh * pence / 100, 2) if _is_number(kwh) and _is_number(pence) else None


def free_event_result(event: dict[str, Any], meters_end: dict[str, Any] | None, soc_end: float | None,
                      import_pence: float | None) -> dict[str, Any]:
    """Measured outcome of a finished free-electricity event (debug report).

    `sold_kwh` is grid export from the sell start to the free start; `free_import_kwh`
    is grid import during the free period, valued at what it would normally have
    cost (`import_pence`). Missing inputs → None.
    """
    free_import = meter_delta(event.get("meters_at_free_start"), meters_end, "grid_import")
    return {
        "sold_kwh": meter_delta(event.get("meters_at_sell_start"), event.get("meters_at_free_start"), "grid_export"),
        "free_import_kwh": free_import,
        "free_import_value_gbp": _gbp(free_import, import_pence),
        "soc_at_end": soc_end,
    }


def event_report(record_type: str, phase: str, event: dict[str, Any]) -> dict[str, Any]:
    """Debug-channel JSON line for a free event lifecycle point (raw meter readings dropped)."""
    return {"type": record_type, "phase": phase, **{k: v for k, v in event.items() if not k.startswith("meters_")}}


# --------------------------------------------------------------------------- #
# Inverter drift check (phase 31)                                              #
# --------------------------------------------------------------------------- #

DRIFT_CHECK_INTERVAL_HOURS = 2
DRIFT_CHECK_AFTER_PUSH_MINUTES = 10
GRID_CURRENT_DRIFT_TOLERANCE_A = 1.0
_FLUX_TIME_FIELDS = ("startTime", "endTime")


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def flux_drift(expected: list[dict[str, Any]] | None, actual: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per-slot differences between the last pushed fluxProducts and what the plant holds.

    Compares startTime/endTime as minutes (so "02:00" == "02:00:00") and
    targetSoc as a number. A field missing or unparseable on either side is
    skipped rather than reported, so a sparse read never raises a false alarm.
    """
    diffs: list[dict[str, Any]] = []
    for slot, (exp, act) in enumerate(zip(expected or [], actual or []), start=1):
        if not isinstance(exp, dict) or not isinstance(act, dict):
            continue
        for key in (*_FLUX_TIME_FIELDS, "targetSoc"):
            parse = hhmm_to_minutes if key in _FLUX_TIME_FIELDS else _as_float
            e = parse(exp.get(key)) if exp.get(key) is not None else None
            a = parse(act.get(key)) if act.get(key) is not None else None
            if e is not None and a is not None and e != a:
                diffs.append({"slot": slot, "field": key, "expected": exp.get(key), "actual": act.get(key)})
    return diffs


def expected_grid_current_a(gentle: dict[str, Any] | None, today_iso: str) -> float | None:
    """The grid-charge current this integration last set, while it still applies.

    The gentle value while applied; the restored value on the day it was put
    back. Otherwise None: the user owns the setting and nothing is checked.
    """
    gentle = gentle or {}
    if gentle.get("phase") == "applied":
        return _as_float(gentle.get("current_a"))
    if gentle.get("phase") == "restored" and gentle.get("date") == today_iso:
        return _as_float(gentle.get("restore_current_a"))
    return None


def grid_current_drift(expected: float | None, actual: Any) -> list[dict[str, Any]]:
    """A one-item drift list when the inverter's grid-charge current differs from expected."""
    actual_a = _as_float(actual)
    if expected is None or actual_a is None or abs(actual_a - expected) < GRID_CURRENT_DRIFT_TOLERANCE_A:
        return []
    return [{"slot": None, "field": "grid_charge_current_a", "expected": expected, "actual": actual_a}]


# --------------------------------------------------------------------------- #
# Tariff price-change alert (phase 33)                                         #
# --------------------------------------------------------------------------- #

TARIFF_PRICE_TOLERANCE_PENCE = 0.05


def tariff_price_changes(
    configured: dict[str, float | None],
    octopus: dict[str, float | None],
    tolerance: float = TARIFF_PRICE_TOLERANCE_PENCE,
) -> list[tuple[str, float, float]]:
    """(band, configured p, Octopus p) for each band whose prices disagree.

    A band missing or None on either side is ignored, so an unavailable
    Octopus entity never reads as a change to 0p.
    """
    changes = []
    for band, mine in configured.items():
        theirs = octopus.get(band)
        if mine is None or theirs is None:
            continue
        if abs(theirs - mine) > tolerance:
            changes.append((band, round(mine, 2), round(theirs, 2)))
    return changes


# --------------------------------------------------------------------------- #
# Multi-day low-solar planning (phase 39, shadow)                              #
# --------------------------------------------------------------------------- #

MULTI_DAY_MIN_RUN = 2
# Tomorrow's battery need runs from the end of the cheap window to the end of
# the peak (05:00-19:00): the part the overnight charge has to carry.
MULTI_DAY_NEED_HOURS = (PEAK_END_MINUTES - 5 * 60) / 60


def low_solar_run(days_kwh: list[float | None], threshold: float = LOW_SOLAR_THRESHOLD_KWH) -> int:
    """Consecutive low-solar days starting today; a missing forecast ends the run."""
    run = 0
    for kwh in days_kwh:
        if kwh is None or kwh >= threshold:
            break
        run += 1
    return run


def multi_day_peak_floor_soc(
    reserve_soc: int,
    capacity_kwh: float | None,
    tomorrow_load_kwh: float | None,
    tomorrow_solar_kwh: float | None,
    refill_kwh: float | None,
) -> int | None:
    """Lowest SOC the 16:00 peak export should sell to ahead of a dull tomorrow.

    Holds back only what tomorrow's cheap window can't put back: the reserve
    plus (tomorrow's 05:00-19:00 load - its solar - the overnight refill),
    clamped to [reserve, 100]. None when any input is missing (no change).
    """
    if None in (capacity_kwh, tomorrow_load_kwh, tomorrow_solar_kwh, refill_kwh) or not capacity_kwh:
        return None
    shortfall_kwh = max(0.0, tomorrow_load_kwh - tomorrow_solar_kwh - refill_kwh)
    return min(100, max(reserve_soc, math.ceil(reserve_soc + shortfall_kwh / capacity_kwh * 100)))


def multi_day_shadow(
    run_days: int,
    target_soc: int | None = None,
    peak_target_soc: int | None = None,
    floor_soc: int | None = None,
) -> dict[str, Any] | None:
    """What multi-day planning would change today, or None outside a run of ≥2 low days.

    In a run: the 01:55 target goes to 100%, daytime trims stay off and the
    16:00 peak export sells no lower than ``floor_soc``.
    """
    if run_days < MULTI_DAY_MIN_RUN:
        return None
    out: dict[str, Any] = {"run_days": run_days, "trims_off": True}
    if target_soc is not None:
        out.update(live_target_soc=target_soc, would_target_soc=100, changes_target=target_soc < 100)
    if peak_target_soc is not None:
        would = max(peak_target_soc, floor_soc) if floor_soc is not None else peak_target_soc
        out.update(live_peak_target_soc=peak_target_soc, would_peak_target_soc=would,
                   changes_peak=would != peak_target_soc)
    return out


# "Cost of 1 kWh now" sensor (phase 38).
MARGINAL_SOLAR_MIN_EXPORT_W = 100.0


def band_key_at(minutes: int) -> str:
    """The `kpi_prices_pence` import band key for a local minute of day."""
    if 2 * 60 <= minutes < TRIM_FROM_MINUTES:  # Flux off-peak 02:00-05:00
        return "offpeak"
    if PEAK_START_MINUTES <= minutes < PEAK_END_MINUTES:
        return "peak"
    return "day"


def price_at(bands: list[tuple[int, int, float]], minutes: int) -> float | None:
    """Price of the minute band (from `flux_bands_from_charges`) covering ``minutes``; None when none does."""
    for start, end, price in bands:
        inside = start <= minutes < end if start < end else (minutes >= start or minutes < end)
        if inside:
            return price
    return None


def solar_is_spare(grid_w: float | None, pv_w: float | None) -> bool:
    """Exporting and PV covers the export, so the next kWh used is lost solar export."""
    return (
        isinstance(grid_w, (int, float)) and isinstance(pv_w, (int, float))
        and grid_w < -MARGINAL_SOLAR_MIN_EXPORT_W and pv_w >= -grid_w
    )


def marginal_cost_pence(
    minutes: int,
    soc: float | None,
    reserve_soc: float | None,
    solar_spare: bool,
    prices: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    wear_pence: float | None = None,
    efficiency: float = ROUND_TRIP_EFFICIENCY,
) -> dict[str, Any]:
    """{"pence", "source"} for using 1 kWh at ``minutes``; pence None when its price is unknown (never 0p).

    Spare solar costs the export it displaces. Battery above ``reserve_soc``
    costs its off-peak replacement (/ round-trip efficiency + wear), or the
    current export price if higher (that kWh could have been sold). Otherwise
    the grid import price for the band. The off-peak band is always grid.
    """
    band = band_key_at(minutes)
    export = price_at(export_bands, minutes)
    if solar_spare:
        return {"pence": round_or_none(export), "source": "solar"}
    if band != "offpeak" and isinstance(soc, (int, float)) and isinstance(reserve_soc, (int, float)) and soc > reserve_soc:
        offpeak = prices.get("offpeak")
        if offpeak is None:
            return {"pence": None, "source": "battery"}
        cost = offpeak / efficiency + (wear_pence or 0.0)
        if export is not None:
            cost = max(cost, export)
        return {"pence": round(cost, 2), "source": "battery"}
    return {"pence": round_or_none(prices.get(band)), "source": "grid"}


def cheapest_hour_today(
    from_hour: int,
    prices: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    spare_hours: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Earliest cheapest remaining hour today: spare-solar hours cost their export price, others the grid band.

    Hours without a price are skipped; None when no hour is priced.
    """
    spare = {h.get("hour") for h in spare_hours or [] if (h.get("spare_kwh") or 0) > 0}
    best: dict[str, Any] | None = None
    for hour in range(max(0, from_hour), 24):
        minutes = hour * 60
        if hour in spare:
            pence, source = price_at(export_bands, minutes), "solar"
        else:
            pence, source = prices.get(band_key_at(minutes)), "grid"
        if pence is None:
            continue
        if best is None or pence < best["pence"]:
            best = {"hour": f"{hour:02d}:00", "pence": round(pence, 2), "source": source}
    return best


# Value-of-the-system report (phase 37). Same 00:00-22:00 KPI day as day_kpis.
# (start, end, import band, can have solar)
_BASELINE_SEGMENTS = (
    ("00:00", "02:00", "day", False),
    ("02:00", "05:00", "offpeak", False),
    ("05:00", "16:00", "day", True),
    ("16:00", "19:00", "peak", True),
    ("19:00", "22:00", "day", True),
)


def _seg_value(snapshots: dict[str, dict[str, Any]], start: str, end: str, key: str) -> float | None:
    """Meter delta for a segment; the daily meters read 0 at midnight."""
    if start == "00:00":
        return _snap(snapshots, end, key)
    return _snap_delta(snapshots, start, end, key)


def _pence(kwh: float, price: float | None) -> float | None:
    """kWh × price; no energy costs 0 even without a price, otherwise a missing price is None."""
    if kwh <= 0:
        return 0.0
    return None if price is None else kwh * price


def no_battery_baseline(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    wear_pence: float | None = None,
) -> dict[str, float | None]:
    """What 00:00-22:00 cost with the battery vs without it, from the meter snapshots.

    Per Flux segment: solar = load − import + export + battery gain (night
    segments have none). Without a battery the house uses that solar
    directly, imports the rest at the band price and exports the surplus.
    Actual = metered import − export at the same prices. Saving = baseline −
    actual − wear on the kWh the battery took in. Coarse bands overstate
    direct solar use, so the saving is conservative. Any missing snapshot or
    needed price → all None (the day is excluded, never 0).
    """
    empty = {"no_battery_gbp": None, "with_battery_gbp": None, "battery_saving_gbp": None, "battery_cycled_kwh": None}
    if battery_capacity_kwh <= 0:
        return empty
    baseline = actual = cycled = 0.0
    for start, end, band, sunny in _BASELINE_SEGMENTS:
        load = _seg_value(snapshots, start, end, "load_kwh")
        imp = _seg_value(snapshots, start, end, "grid_import_kwh")
        exp = _seg_value(snapshots, start, end, "grid_export_kwh")
        if load is None or imp is None or exp is None:
            return empty
        soc_a, soc_b = _snap(snapshots, start, "soc"), _snap(snapshots, end, "soc")
        gain = None if soc_a is None or soc_b is None else (soc_b - soc_a) / 100 * battery_capacity_kwh
        if sunny:
            if gain is None:
                return empty
            solar = max(0.0, load - imp + exp + gain)
        else:
            solar = 0.0
        if gain is not None and gain > 0:
            cycled += gain
        direct = min(solar, load)
        imp_price = prices_pence.get(band)
        exp_price = price_at(export_bands, hhmm_to_minutes(start))
        parts = (
            _pence(load - direct, imp_price), _pence(solar - direct, exp_price),
            _pence(imp, imp_price), _pence(exp, exp_price),
        )
        if any(p is None for p in parts):
            return empty
        baseline += parts[0] - parts[1]
        actual += parts[2] - parts[3]
    wear = cycled * (wear_pence or 0.0)
    return {
        "no_battery_gbp": round(baseline / 100, 2),
        "with_battery_gbp": round(actual / 100, 2),
        "battery_saving_gbp": round((baseline - actual - wear) / 100, 2),
        "battery_cycled_kwh": round(cycled, 2),
    }


def battery_value_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    """{"saving_gbp", "days"} over days with a `battery_saving_gbp`; saving None when none has one."""
    values = [d["battery_saving_gbp"] for d in days if isinstance(d.get("battery_saving_gbp"), (int, float))]
    return {"saving_gbp": round(sum(values), 2) if values else None, "days": len(values)}


# Battery capacity tracking (phase 34). Shadow: never changes battery_capacity.
CAPACITY_MIN_SWING_SOC = 30.0
CAPACITY_MIN_SAMPLES = 5
CAPACITY_ALERT_FRACTION = 0.9
CAPACITY_DISCHARGE_MONTHS = (10, 11, 12, 1, 2, 3)  # no solar after 19:00
CAPACITY_HISTORY_DAYS = 60


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


def record_for_date(record: dict[str, Any] | None, date: str) -> dict[str, Any]:
    """The stored record if it is for `date`, else {} (a stale day is never re-sent)."""
    return record if record and record.get("date") == date else {}


# Bill reconciliation (phase 36).
BILL_GAP_ALERT_GBP = 2.0


def logged_day_cost_gbp(day: dict[str, Any], include_gas: bool) -> float | None:
    """A logged day's net £ (import − export, + gas when configured); None when a needed field is missing."""
    imp, exp, gas = (day.get(k) for k in ("actual_import_cost_gbp", "actual_export_income_gbp", "actual_gas_cost_gbp"))
    if not _is_number(imp) or not _is_number(exp):
        return None
    if not include_gas:
        return round(imp - exp, 2)
    return net_cost_gbp(imp, exp, gas) if _is_number(gas) else None


BILL_ATTRIBUTE_KEYS = (
    "logged_gbp", "billed_gbp", "gap_gbp", "gap_pct", "days_logged", "days_in_month", "partial", "alert", "includes_gas",
)


def bill_attributes(state: dict[str, Any] | None) -> dict[str, Any]:
    """Billed-amount entity attributes: every key always present (None until a bill is reconciled)."""
    state = state or {}
    result = state.get("result") or {}
    return {"month": state.get("month"), **{k: result.get(k) for k in BILL_ATTRIBUTE_KEYS}}


def reconcile_month(
    day_costs: list[float | None],
    days_in_month: int,
    billed_gbp: float | None,
    standing_p_per_day: float | None = None,
    threshold_gbp: float = BILL_GAP_ALERT_GBP,
) -> dict[str, Any]:
    """Compare a month's logged cost with the billed £.

    ``day_costs`` is one value per logged day (None = that day's cost is
    missing). ``standing_p_per_day`` is added per day only when the logged
    costs exclude it (None = already included, as the Octopus cost sensors
    do). A month with missing days is ``partial`` and never alerts; unknown
    billed → gap None, no alert.
    """
    values = [c for c in day_costs if _is_number(c)]
    logged = sum(values) + (standing_p_per_day or 0.0) * len(values) / 100 if values else None
    partial = len(values) < days_in_month
    gap = None if logged is None or billed_gbp is None else billed_gbp - logged
    pct = None if gap is None or not billed_gbp else gap / abs(billed_gbp) * 100
    return {
        "logged_gbp": round_or_none(logged),
        "billed_gbp": round_or_none(billed_gbp),
        "gap_gbp": round_or_none(gap),
        "gap_pct": round_or_none(pct, 1),
        "days_logged": len(values),
        "days_in_month": days_in_month,
        "partial": partial,
        "alert": gap is not None and not partial and abs(gap) > threshold_gbp,
    }
