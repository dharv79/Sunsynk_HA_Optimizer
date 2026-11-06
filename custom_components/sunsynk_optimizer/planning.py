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
from urllib.parse import quote
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
    config_rate_kw: float,
    effective_rate_kw: float | None,
    battery_temp_c: float,
    derate: bool = True,
) -> tuple[float, float]:
    """Return (used_charge_rate_kw, temp_deration_factor).

    The learned rate replaces nameplate only when it's meaningfully (>10%)
    lower; either way the result is then derated for battery temperature.
    ``derate=False`` (phase 61) is for a rate already measured at tonight's
    temperature band: the factor is still returned for the log but not applied.
    """
    rate = config_rate_kw
    if effective_rate_kw is not None and effective_rate_kw < config_rate_kw * 0.9:
        rate = min(config_rate_kw, effective_rate_kw)
    factor = temp_deration_factor(battery_temp_c)
    return round(rate * (factor if derate else 1.0), 2), factor


# Phase 61: measure the charge rate directly from the 02:00 and 05:00 meters.
MEASURED_RATE_MIN_SAMPLES = 3
MEASURED_RATE_NIGHTS = 14
MEASURED_RATE_PERCENTILE = 75   # capped nights read low, so lean high
MEASURED_RATE_MIN_WINDOW_H = 0.5
MEASURED_RATE_MAX_KW = 6.0      # above any plausible home battery: a bad meter reading


def measured_charge_kw(
    grid_import_kwh: float | None,
    load_kwh: float | None,
    flux1_end: str | None,
    initial_soc: float | None,
    target_soc: float | None,
    soc_cached: bool = False,
) -> float | None:
    """Battery-side charge kW from the 02:00-05:00 meter deltas, else None.

    Grid import over the window minus the house load that fell inside it
    (load is spread evenly over the 3 h, so a window ending before 05:00 only
    takes its share). A window that runs to 05:00 is skipped: the battery stops
    at target well before the end, so the divisor would be wrong. A window
    longer than the charge needed reads low, but that shrinks the next window,
    so the estimate climbs to the true rate rather than sticking.
    """
    if None in (grid_import_kwh, load_kwh, initial_soc, target_soc) or not flux1_end or soc_cached:
        return None
    end_min = hhmm_to_minutes(flux1_end)
    if end_min is None or end_min >= 5 * 60:
        return None
    window_h = (end_min - FLUX1_START_MINUTES) / 60.0
    if window_h < MEASURED_RATE_MIN_WINDOW_H or target_soc - initial_soc < 5:
        return None
    net_kwh = grid_import_kwh - max(0.0, load_kwh) * window_h / 3.0
    if net_kwh <= 0:
        return None
    rate = net_kwh * CHARGE_EFFICIENCY / window_h
    return round(rate, 2) if rate <= MEASURED_RATE_MAX_KW else None


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def learned_measured_rate(
    samples: list[tuple[float, float]], tonight_temp_c: float
) -> tuple[float, bool] | None:
    """(rate_kw, already_at_tonights_temperature) from (kw, battery_temp_c) samples.

    Samples in tonight's derate band give the rate directly, so the caller must
    not derate it again. Otherwise every sample is normalised to the warm rate
    (kw / its band's factor) and the caller derates as usual. None until 3 samples.
    """
    if len(samples) < MEASURED_RATE_MIN_SAMPLES:
        return None
    tonight = temp_deration_factor(tonight_temp_c)
    same = [kw for kw, t in samples if temp_deration_factor(t) == tonight]
    if len(same) >= MEASURED_RATE_MIN_SAMPLES:
        return round(_percentile(same, MEASURED_RATE_PERCENTILE), 2), True
    warm = [kw / temp_deration_factor(t) for kw, t in samples]
    return round(_percentile(warm, MEASURED_RATE_PERCENTILE), 2), False


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


# A plan skipped because the SOC sensor is unavailable (SolarSynkV3 sometimes
# drops out around 02:00) re-runs every 5 minutes until 02:30.
SOC_RETRY_INTERVAL_MINUTES = 5
SOC_RETRY_UNTIL_MINUTES = 2 * 60 + 30
# A meter snapshot with no live SOC uses the last reading up to this old.
SNAPSHOT_SOC_MAX_AGE_MINUTES = 30


def soc_retry_due(now_minutes: int) -> bool:
    """Whether to re-run a plan skipped for an unavailable SOC in SOC_RETRY_INTERVAL_MINUTES."""
    next_minutes = now_minutes + SOC_RETRY_INTERVAL_MINUTES
    return PLAN_RETRY_FROM_MINUTES <= next_minutes <= SOC_RETRY_UNTIL_MINUTES


def recent_soc(
    current: float | None,
    last: tuple[float, datetime] | None,
    now: datetime,
    max_age_minutes: int = SNAPSHOT_SOC_MAX_AGE_MINUTES,
) -> tuple[float | None, bool]:
    """(SOC, from_cache): the live reading, else `last` (value, time) if at most `max_age_minutes` old."""
    if current is not None:
        return current, False
    if last is None:
        return None, False
    value, at = last
    if 0 <= (now - at).total_seconds() <= max_age_minutes * 60:
        return value, True
    return None, False
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


# Phase 64: newsvendor choice of the forecast scale for the hourly solar bridge.
# Shadow only: logged beside the live target, never pushed.
NEWSVENDOR_MIN_DAYS = 20
NEWSVENDOR_CR_BOUNDS = (0.2, 0.8)   # keep the quantile off the extreme tails
NEWSVENDOR_MIN_OVERBUY_P = 0.1      # over-buy cost floor so a free surplus can't push CR to 1


def newsvendor_costs(
    day_p: float | None, offpeak_p: float | None, export_p: float | None,
    efficiency: float = CHARGE_EFFICIENCY, wear_pence: float | None = None,
) -> tuple[float, float] | None:
    """(under-buy, over-buy) p per battery kWh, or None when a price is missing (never 0p).

    Under-buying a kWh means importing it at the day rate instead of the cheap
    rate; over-buying means paying the cheap rate for energy that ends up
    exported at the peak export rate (plus wear on the extra cycle).
    """
    if None in (day_p, offpeak_p, export_p) or efficiency <= 0:
        return None
    cheap = offpeak_p / efficiency
    under = day_p - cheap
    over = cheap - export_p + (wear_pence or 0.0)
    if under <= 0:
        return None
    return round(under, 3), round(max(over, NEWSVENDOR_MIN_OVERBUY_P), 3)


def newsvendor_quantile(under_p: float, over_p: float) -> float:
    """Quantile of the actual/forecast ratio to plan at.

    A higher ratio means more solar and a smaller bridge, so the demand-side
    critical ratio under/(under+over) maps to the (1 - CR) ratio quantile.
    """
    cr = under_p / (under_p + over_p)
    cr = max(NEWSVENDOR_CR_BOUNDS[0], min(NEWSVENDOR_CR_BOUNDS[1], cr))
    return round(1.0 - cr, 3)


def quantile_correction(
    paired_days: list[dict[str, Any]],
    today: date,
    quantile: float,
    band: str | None = None,
    half_life_days: float = FORECAST_CORRECTION_HALF_LIFE_DAYS,
    min_days: int = NEWSVENDOR_MIN_DAYS,
) -> tuple[float | None, int]:
    """(recency-weighted quantile of actual/forecast, days used), or (None, n) below ``min_days``.

    Same ratio rules as weighted_forecast_correction (forecast <= 0.5 kWh and
    missing actuals skipped, capped 0.5-3.0). Uses in-band days when they reach
    ``min_days``, else all days; thin history yields None, not a guess.
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
    in_band = [v for v in valid if band is not None and v[2] == band]
    if len(in_band) >= min_days:
        valid = in_band
    if len(valid) < min_days:
        return None, len(valid)
    valid.sort(key=lambda v: v[0])
    target = sum(w for _, w, _ in valid) * max(0.0, min(1.0, quantile))
    running, ratio = 0.0, valid[-1][0]
    for r, weight, _ in valid:
        running += weight
        if running >= target:
            ratio = r
            break
    return max(0.5, min(3.0, round(ratio, 3))), len(valid)


def newsvendor_target(
    hourly_forecast_kwh: dict[int, float] | None,
    avg_consumption_kw: float,
    battery_capacity_kwh: float,
    ratio: float | None,
) -> int | None:
    """Pre-adjustment hourly-bridge SOC at the newsvendor ``ratio``; None when it can't be computed."""
    if not hourly_forecast_kwh or ratio is None:
        return None
    gap, _hour = walk_bridge_gap(hourly_forecast_kwh, avg_consumption_kw, ratio)
    return bridge_soc(gap, battery_capacity_kwh)


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
def drain_pct_to_0500(
    target_soc: float | None,
    soc_0500: float | None,
    drain_0600: float | None,
    flux1_end: str | None,
) -> float | None:
    """Overnight drain (SOC points) measured to 05:00, where the solar bridge starts counting.

    The bridge sizes load from 05:00, so a drain measured to 06:00 double counts
    05:00-06:00 (phase 63). Prefer the 05:00 meter snapshot SOC; otherwise scale
    the 06:00 drain by (window-1)/window, where window is the hours from the
    Flux 1 end to 06:00. Missing inputs give None, never 0 (rule 11). A window
    of 1h or less, or an unparseable end time, keeps the 06:00 figure.
    """
    if target_soc is not None and soc_0500 is not None:
        return round(target_soc - soc_0500, 1)
    if drain_0600 is None:
        return None
    try:
        hh, mm = str(flux1_end).split(":")[:2]
        window = 6.0 - (int(hh) + int(mm) / 60.0)
    except (ValueError, TypeError):
        return drain_0600
    if window <= 1.0:
        return drain_0600
    return round(drain_0600 * (window - 1.0) / window, 1)


SHORTFALL_BASE_PCT = 3.0       # SOC points of drain tolerated even with no gap after the charge
SHORTFALL_PCT_PER_HOUR = 5.0   # plausible overnight drain per hour between Flux 1 end and 05:00


def charge_shortfall_suspect(drain_0500: float | None, flux1_end: str | None) -> bool | None:
    """True when a target-to-05:00 gap is too big to be pure drain (phase 53).

    The gap target - SOC@05:00 is drain only if the charge reached target. After
    a charge that stopped short the same gap is shortfall, and learning it as
    drain inflates the buffer. Allowed drain is a base plus a per-hour rate for
    the hours between the Flux 1 end and 05:00. Unknown inputs give None.
    """
    if drain_0500 is None:
        return None
    try:
        hh, mm = str(flux1_end).split(":")[:2]
        hours = max(0.0, 5.0 - (int(hh) + int(mm) / 60.0))
    except (ValueError, TypeError):
        return None
    return drain_0500 > SHORTFALL_BASE_PCT + SHORTFALL_PCT_PER_HOUR * hours


OUTCOME_ALERT_PCT = 5.0        # notify when the charge ended this many points below target
OUTCOME_TAPER_FROM_PCT = 92.0  # targets this high usually taper at the top
OUTCOME_TAPER_MAX_GAP = 8.0
OUTCOME_COLD_C = 10.0


def charge_outcome(
    target_soc: float | None,
    reached_soc: float | None,
    battery_temp_c: float | None = None,
) -> dict[str, Any] | None:
    """Reached-vs-target result of the overnight charge (phase 54); None when unknown.

    shortfall_pct is target - reached (negative = overshoot). The cause is a
    heuristic label: taper above ~92% for a small gap on a high target, cold
    derate below 10 C, otherwise the inverter stopped early.
    """
    if target_soc is None or reached_soc is None:
        return None
    gap = round(target_soc - reached_soc, 1)
    cause = None
    if gap > 0:
        if target_soc >= OUTCOME_TAPER_FROM_PCT and gap <= OUTCOME_TAPER_MAX_GAP:
            cause = "taper"
        elif battery_temp_c is not None and battery_temp_c < OUTCOME_COLD_C:
            cause = "cold_derate"
        else:
            cause = "stopped_early"
    return {
        "target_soc": target_soc,
        "reached_soc": reached_soc,
        "shortfall_pct": gap,
        "likely_cause": cause,
        "alert": gap > OUTCOME_ALERT_PCT,
    }


def achievable_max_soc(outcomes: list[dict[str, Any]], min_target: float = 95.0) -> float | None:
    """Median SOC reached on nights that aimed for >= min_target (shadow, phase 54)."""
    vals = sorted(
        o["reached_soc"] for o in outcomes
        if o.get("reached_soc") is not None and (o.get("target_soc") or 0) >= min_target
    )
    if not vals:
        return None
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else round((vals[mid - 1] + vals[mid]) / 2, 1)


def monthly_report_due(last_month: str | None, today: date) -> bool:
    """True when last month's reports are unsent and the first Sunday has passed (phase 57).

    Covers the report month (the one before ``today``'s). Not due before this
    month's first Sunday, so the normal Sunday run is unchanged; any later day
    catches up a missed Sunday.
    """
    report_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    if last_month == report_month:
        return False
    first_sunday = 1 + (6 - today.replace(day=1).weekday()) % 7
    return today.day >= first_sunday


KPI_VERSION = 2  # 2 = phase 52 self-sufficiency (excludes battery grid-charge and free import)
DAY_KPI_FIELDS = (
    "grid_import_offpeak_kwh",
    "grid_import_day_kwh",
    "grid_import_morning_kwh",
    "grid_import_peak_kwh",
    "grid_import_evening_kwh",
    "self_sufficiency_pct",
    "grid_independence_pct",  # phase 52: the pre-v2 ratio (all import, incl. battery charging)
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


def _credit(free_credit: dict[str, tuple[float, float]] | None, segment: str, index: int = 0) -> float:
    return (free_credit or {}).get(segment, (0.0, 0.0))[index]


def _less(value: float | None, credit: float) -> float | None:
    return None if value is None else max(0.0, value - credit)


# Import while the battery still had charge and was discharging is load above
# the inverter's output (or a short spike), not a planning shortfall.
OVER_LIMIT_MARGIN_SOC = 5  # above KPI_RESERVE_SOC, so a battery near its floor still counts
OVER_LIMIT_MAX_GAP_MIN = 35  # samples further apart (restart) prove nothing


def kpi_segment(minute: int) -> str | None:
    """The `_BASELINE_SEGMENTS` start ("HH:MM") a minute of the day falls in; None from 22:00."""
    for start, end, _band, _ in _BASELINE_SEGMENTS:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
        if sh * 60 + sm <= minute < eh * 60 + em:
            return start
    return None


def over_limit_import_step(prev: dict[str, Any] | None, sample: dict[str, Any]) -> tuple[str | None, float]:
    """Over-limit import between two samples {minute, soc, grid_import_kwh}: (segment, kWh).

    Samples are taken on every SOC change, so if both ends sit above the
    reserve + margin the battery never reached its floor in between. Import
    while the SOC fell (the battery was already discharging into the house)
    is load it could not cover. A flat SOC proves nothing — a slot target can
    hold the battery while the house runs on the grid — and a rising one is a
    grid charge. Anything unknown, a long gap or a meter reset → 0.
    """
    if prev is None:
        return None, 0.0
    values = (prev.get("minute"), prev.get("soc"), prev.get("grid_import_kwh"),
              sample.get("minute"), sample.get("soc"), sample.get("grid_import_kwh"))
    if any(v is None for v in values):
        return None, 0.0
    m0, soc0, imp0, m1, soc1, imp1 = values
    floor = KPI_RESERVE_SOC + OVER_LIMIT_MARGIN_SOC
    if not 0 < m1 - m0 <= OVER_LIMIT_MAX_GAP_MIN or imp1 < imp0:
        return None, 0.0
    if min(soc0, soc1) <= floor or soc1 >= soc0:
        return None, 0.0
    segment = kpi_segment(m0)
    return segment, (imp1 - imp0) if segment else 0.0


IMPORT_CAUSES = ("free_event", "grid_charge", "load_over_limit", "battery_floor", "slot_hold", "unknown")
IMPORT_CAUSE_CHARGE_WINDOW = (2 * 60, 5 * 60)


def import_cause_step(prev: dict[str, Any] | None, sample: dict[str, Any]) -> tuple[str | None, float]:
    """Assign the grid import between two samples to one cause (phase 42): (cause, kWh).

    Samples {minute, soc, grid_import_kwh, slots_held, slot_hold}. A long gap, a meter reset
    or any unknown reading is not attributed (None, 0.0) - never guessed. Order of evidence:
    an event holding the slots; a rising SOC (grid charge); a falling SOC above the floor
    (load above what the inverter can discharge, as over_limit_import_step); SOC at the floor;
    a flat SOC while a slot target is known to hold the battery; otherwise 'unknown'.
    """
    if prev is None:
        return None, 0.0
    values = (prev.get("minute"), prev.get("soc"), prev.get("grid_import_kwh"),
              sample.get("minute"), sample.get("soc"), sample.get("grid_import_kwh"))
    if any(v is None for v in values):
        return None, 0.0
    m0, soc0, imp0, m1, soc1, imp1 = values
    if not 0 < m1 - m0 <= OVER_LIMIT_MAX_GAP_MIN or imp1 < imp0:
        return None, 0.0
    kwh = imp1 - imp0
    if kwh <= 0:
        return None, 0.0
    if sample.get("slots_held") or prev.get("slots_held"):
        return "free_event", kwh
    if soc1 > soc0:
        return "grid_charge", kwh
    floor = KPI_RESERVE_SOC + OVER_LIMIT_MARGIN_SOC
    if soc1 < soc0 and min(soc0, soc1) > floor:
        return "load_over_limit", kwh
    if min(soc0, soc1) <= floor:
        return "battery_floor", kwh
    if sample.get("slot_hold") or IMPORT_CAUSE_CHARGE_WINDOW[0] <= m0 < IMPORT_CAUSE_CHARGE_WINDOW[1]:
        return "slot_hold", kwh
    return "unknown", kwh


def day_kpis(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    battery_capacity_kwh: float,
    late_load_kw: float | None,
    free_credit: dict[str, tuple[float, float]] | None = None,
    over_limit: dict[str, float] | None = None,
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
    - `free_credit` (free_event_kpi_credit): free-event import per segment,
      left out of the import bands and avoidable £ (it cost nothing).
    - `over_limit` (segment → kWh, over_limit_import_step): import while the
      battery still had charge; stays in the bands (it was paid for) but is
      left out of avoidable £ (no plan could have shifted it).
    """
    imp = "grid_import_kwh"
    offpeak = _less(_snap_delta(snapshots, "02:00", "05:00", imp), _credit(free_credit, "02:00"))
    peak = _less(_snap_delta(snapshots, "16:00", "19:00", imp), _credit(free_credit, "16:00"))
    morning = _less(_snap_delta(snapshots, "05:00", "16:00", imp), _credit(free_credit, "05:00"))
    evening = _less(_snap_delta(snapshots, "19:00", "22:00", imp), _credit(free_credit, "19:00"))
    parts = (_less(_snap(snapshots, "02:00", imp), _credit(free_credit, "00:00")), morning, evening)
    day = None if any(p is None for p in parts) else sum(parts)

    load22, imp22 = _snap(snapshots, "22:00", "load_kwh"), _snap(snapshots, "22:00", imp)
    self_sufficiency = grid_independence = None
    soc02_ss, soc05_ss = _snap(snapshots, "02:00", "soc"), _snap(snapshots, "05:00", "soc")
    if load22 is not None and imp22 is not None and load22 > 0:
        grid_independence = max(0.0, min(100.0, 100.0 * (1 - imp22 / load22)))
        if soc02_ss is not None and soc05_ss is not None and battery_capacity_kwh > 0:
            # Phase 52: paid import that served the house = total less free import and
            # the 02:00-05:00 battery gain (grid-charged, not consumed).
            free_kwh = sum(c[0] for c in (free_credit or {}).values())
            gain_kwh = max(0.0, soc05_ss - soc02_ss) / 100 * battery_capacity_kwh
            house_import = max(0.0, imp22 - free_kwh - gain_kwh)
            self_sufficiency = max(0.0, min(100.0, 100.0 * (1 - house_import / load22)))

    avoidable_pence = over_limit_kwh = None
    over = over_limit or {}
    if day is not None and peak is not None:
        avoid_day = sum(_less(p, over.get(seg, 0.0)) for p, seg in zip(parts, ("00:00", "05:00", "19:00")))
        avoid_peak = _less(peak, over.get("16:00", 0.0))
        over_limit_kwh = day + peak - avoid_day - avoid_peak
        avoidable_pence = _mul(avoid_day, prices_pence.get("day"))
        peak_pence = _mul(avoid_peak, prices_pence.get("peak"))
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
        "grid_independence_pct": round_or_none(grid_independence, 1),
        "kpi_version": KPI_VERSION,
        "avoidable_import_gbp": round_or_none(None if avoidable_pence is None else avoidable_pence / 100),
        "export_peak_kwh": round_or_none(export_peak),
        "export_peak_gbp": round_or_none(None if export_peak_pence is None else export_peak_pence / 100),
        "unused_charge_kwh": round_or_none(unused),
        "free_import_excluded_kwh": round(sum(c[0] for c in (free_credit or {}).values()), 2),
        "over_limit_import_kwh": round_or_none(over_limit_kwh),
    }


SOC_CAP_SHADOW_SOC = 85


def soc_cap_shadow(
    plan: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    battery_capacity_kwh: float,
    late_load_kw: float | None,
    prices_pence: dict[str, float | None],
    cap_soc: int = SOC_CAP_SHADOW_SOC,
) -> dict[str, Any] | None:
    """Shadow only: what capping tonight's low-solar target at `cap_soc` would have cost.

    None unless the 01:55 plan was a low-solar override above the cap (the
    weekly full-charge day is left out). The top `cut_soc` (charge reached
    above the cap) was needed only if the day's lowest SOC — snapshots from
    05:00 plus 22:00 projected to the 02:00 charge at `late_load_kw` — came
    within `cut_soc` of the discharge floor; that shortfall would have been
    bought at the day rate instead of off-peak. Missing inputs → None fields.
    """
    target = plan.get("target_soc")
    if not str(plan.get("target_soc_reason") or "").startswith("low_solar_override"):
        return None
    if not isinstance(target, (int, float)) or target <= cap_soc:
        return None
    soc05, soc22 = _snap(snapshots, "05:00", "soc"), _snap(snapshots, "22:00", "soc")
    reached = min(target, soc05) if soc05 is not None else target
    cut_soc = max(0.0, reached - cap_soc)
    lowest = shortfall_kwh = extra_gbp = None
    if soc22 is not None and late_load_kw is not None and battery_capacity_kwh > 0:
        end_soc = soc22 - late_load_kw * KPI_LATE_LOAD_HOURS / battery_capacity_kwh * 100
        socs = [s for s in (_snap(snapshots, t, "soc") for t in ("05:00", "16:00", "19:00")) if s is not None]
        lowest = min([*socs, end_soc])
        headroom = max(0.0, lowest - BATTERY_LOW_SOC)
        shortfall_kwh = max(0.0, cut_soc - headroom) / 100 * battery_capacity_kwh
        day, offpeak = prices_pence.get("day"), prices_pence.get("offpeak")
        if day is not None and offpeak is not None:
            extra_gbp = shortfall_kwh * (day - offpeak) / 100
    return {
        "target_soc": target,
        "cap_soc": cap_soc,
        "cut_kwh": round(cut_soc / 100 * battery_capacity_kwh, 2),
        "lowest_soc": round_or_none(lowest, 1),
        "shortfall_kwh": round_or_none(shortfall_kwh),
        "extra_cost_gbp": round_or_none(extra_gbp),
    }


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def explain_plan(plan: dict[str, Any] | None, shadow: dict[str, Any] | None = None) -> list[str]:
    """Plain-English sentences saying why the import plan chose its target (phase 58).

    Built only from the plan's own fields (reason code, forecast, adjustments,
    free-event cap, full-charge day), so it cannot drift from the decision.
    Missing fields are left out, never guessed as 0. ``shadow`` is a
    ``soc_cap_shadow`` result (known only after the day closes) for the 85%
    counterfactual.
    """
    if not isinstance(plan, dict):
        return []
    target = _num(plan.get("target_soc"))
    if target is None:
        return []
    reason = str(plan.get("target_soc_reason") or "")
    out: list[str] = []
    forecast = _num(plan.get("solar_forecast_kwh"))
    low = _num(plan.get("low_solar_forecast_kwh"))
    raw = _num(plan.get("raw_forecast_kwh"))
    low_txt = f"{low:.1f} kWh" if low is not None else "the forecast"
    if reason == "weekly_full_charge_day":
        out.append(f"Target {target:.0f}%: this is the weekly full-charge day, so the battery is charged for the scoring run.")
    elif reason == "low_solar_override_winter_like":
        out.append(
            f"Target {target:.0f}%: low-solar winter night — the pessimistic forecast of {low_txt} is below the "
            f"{LOW_SOLAR_THRESHOLD_KWH:.0f} kWh line, so the battery is filled from cheap import."
        )
    elif reason == "low_solar_override":
        out.append(
            f"Target {target:.0f}%: the pessimistic forecast of {low_txt} is below the "
            f"{LOW_SOLAR_THRESHOLD_KWH:.0f} kWh line, so the battery is filled from cheap import."
        )
    elif reason in ("solar_bridge_hourly", "solar_bridge", "solar_bridge_ramp"):
        hour = plan.get("bridge_hour")
        until = f" until solar covers the house around {int(hour):02d}:00" if isinstance(hour, int) else " until solar takes over"
        extra = " (raised for a slow-ramp morning)" if reason == "solar_bridge_ramp" else ""
        out.append(f"Target {target:.0f}%: enough charge to bridge the house load{until}{extra}.")
    else:
        out.append(f"Target {target:.0f}%: no solar start time, so the {str(plan.get('forecast_band') or 'seasonal').replace('_', '-')} default is used.")
    if forecast is not None:
        factor = _num(plan.get("forecast_correction_factor"))
        if raw is not None and factor not in (None, 1.0):
            out.append(f"Forecast {forecast:.1f} kWh (raw {raw:.1f} kWh x {factor}); low-solar decisions use the lower of raw and corrected.")
        else:
            out.append(f"Forecast {forecast:.1f} kWh.")
    drain, evening = _num(plan.get("overnight_drain_adjustment")), _num(plan.get("soc_adjustment"))
    if drain:
        out.append(f"+{drain:.0f}% for overnight drain learned from past nights.")
    if evening:
        out.append(f"{evening:+.0f}% evening nudge from how past days ended.")
    feedback = _num(plan.get("import_feedback_adjustment"))
    if feedback:
        live = plan.get("import_feedback_live")
        out.append(f"Import feedback suggests {feedback:+.0f}% ({'applied' if live else 'logged only'}).")
    if plan.get("free_event_capped"):
        out.append("Capped by a free-electricity event, which will refill the battery.")
    if plan.get("logic_branch") == "full_window":
        out.append("Charging is held open to 05:00 so the grid, not the battery, runs the house.")
    if isinstance(shadow, dict):
        cap, extra_cost = _num(shadow.get("cap_soc")), _num(shadow.get("extra_cost_gbp"))
        if cap is not None and extra_cost is not None:
            out.append(f"Shadow: capping at {cap:.0f}% would have cost about £{extra_cost:.2f} extra.")
        elif cap is not None:
            out.append(f"Shadow: capping at {cap:.0f}% could not be costed (missing data).")
    return out


def week_kpis(days: list[dict[str, Any]]) -> dict[str, float | None]:
    """Weekly roll-up: kWh/£ fields summed, self-sufficiency averaged over days that have it."""
    pct = ("self_sufficiency_pct", "grid_independence_pct")
    out = {f"week_{key}": sum_field(days, key) for key in DAY_KPI_FIELDS if key not in pct}
    for key in pct:
        vals = [d[key] for d in days if d.get(key) is not None]
        out[f"week_{key}"] = round(sum(vals) / len(vals), 1) if vals else None
    ss = [d["self_sufficiency_pct"] for d in days if d.get("self_sufficiency_pct") is not None]
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


# Phase 55: evening load learned from the 16:00 -> 22:00 meter snapshots.
EVENING_LOAD_HOURS = 6.0
EVENING_LOAD_WINDOW_DAYS = 28
EVENING_LOAD_MIN_DAYS = 7


def learned_evening_load_kw(
    snapshot_days: dict[str, dict[str, dict[str, Any]]],
    today: date,
    weekend: bool | None = None,
    skip_dates: set[str] | frozenset[str] = frozenset(),
    window_days: int = EVENING_LOAD_WINDOW_DAYS,
    min_days: int = EVENING_LOAD_MIN_DAYS,
) -> tuple[float | None, int]:
    """(median 16:00-22:00 kW, days used), or (None, n) below ``min_days``.

    Needs both the 16:00 and 22:00 load snapshots (a missing or backwards meter
    skips the day, never counts as 0). ``skip_dates`` drops away days. With
    ``weekend`` set, uses matching days when there are ``min_days`` of them.
    """
    rates: list[tuple[float, bool]] = []
    for day_str, snapshots in snapshot_days.items():
        if day_str in skip_dates:
            continue
        delta = _snap_delta(snapshots, "16:00", "22:00", "load_kwh")
        if not delta or delta <= 0:
            continue
        try:
            day = date.fromisoformat(day_str)
        except (TypeError, ValueError):
            continue
        if not 0 < (today - day).days <= window_days:
            continue
        rates.append((delta / EVENING_LOAD_HOURS, day.weekday() >= 5))
    if weekend is not None:
        matching = [r for r in rates if r[1] == weekend]
        if len(matching) >= min_days:
            rates = matching
    if len(rates) < min_days:
        return None, len(rates)
    return round(_median([r for r, _ in rates]), 3), len(rates)


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


FULL_CHARGE_REACHED_SOC = 99.0
FULL_CHARGE_MAX_GAP_DAYS = 7
FULL_CHARGE_HARD_MAX_GAP_DAYS = 14  # cell balancing: never skip beyond this


def days_since_full(snapshot_days: dict[str, dict[str, dict]], today, reached_soc: float = FULL_CHARGE_REACHED_SOC) -> int | None:
    """Days since any meter snapshot (02/05/16/19/22:00) read >= 99% SOC; None when none in the history (phase 68)."""
    latest = None
    for day, snaps in snapshot_days.items():
        try:
            d = date.fromisoformat(day)
        except (TypeError, ValueError):
            continue
        if d > today:
            continue
        if any(isinstance(sn.get("soc"), (int, float)) and sn["soc"] >= reached_soc for sn in (snaps or {}).values()):
            latest = d if latest is None or d > latest else latest
    return None if latest is None else (today - latest).days


def full_charge_due(days_since: int | None, max_gap_days: int = FULL_CHARGE_MAX_GAP_DAYS) -> tuple[bool, str]:
    """Is the weekly forced 100% still needed? Unknown history -> due. Gap capped at 14 days (phase 68)."""
    gap = min(max_gap_days, FULL_CHARGE_HARD_MAX_GAP_DAYS)
    if days_since is None:
        return True, "no_history"
    if days_since < gap:
        return False, "recent_full_skip"
    return True, "gap_reached"


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
    peak_need_soc: float = 0.0,
) -> dict[str, Any]:
    """16:00 decision: export the SOC above reserve + peak need + margin at the peak rate.

    `peak_need_soc` is the SOC the house will use 16:00-19:00 (`peak_need_soc()`),
    so the battery still reaches 19:00 at the evening reserve instead of the
    peak load being bought at the peak rate. The kWh sold would otherwise
    reduce tomorrow's off-peak charge, so it is worth it only if the peak
    export price beats the off-peak replacement cost after round-trip losses.
    `decision` is export / no_surplus / not_worth_it / no_price; gain is export
    income minus replacement cost.
    """
    target = min(100, math.ceil(reserve_soc + peak_need_soc + margin_soc))
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
) -> dict[str, Any]:
    """17:00/18:00 re-check of the peak export target from the load seen since 16:00 (phase 66).

    The 16:00 plan sized the peak load from history. The rest of the window is
    sized from the observed rate instead (x PEAK_RECHECK_MARGIN) and run through
    `peak_export_plan`, so the target never falls below reserve + margin. A push
    is proposed only when it moves the target by >= PEAK_RECHECK_MIN_KWH versus
    the 16:00 target: lower (sell more) when the load is lighter than assumed,
    higher (stop selling) only if the 16:00 plan was actually pushed. Missing
    soc or observed load is None, never 0.
    """
    base: dict[str, Any] = {
        "decision": None, "action": None, "target_soc": None, "prior_target_soc": prior_target_soc,
        "move_kwh": None, "export_kwh": 0.0, "gain_gbp": None,
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
        "export_kwh": plan["export_kwh"], "gain_gbp": plan["gain_gbp"],
        "remaining_load_kwh": round(remaining_kwh, 2),
    }


# Phase 30: peak cover — make sure the battery reaches 16:00 with enough charge
# to run the house through 16:00-19:00, topping up at the (cheaper) day rate.
PEAK_COVER_FLOOR_SOC = BATTERY_LOW_SOC  # full-power discharge through the peak
PEAK_COVER_MARGIN = 1.1
PEAK_COVER_MIN_SHORTFALL_SOC = 3
PEAK_COVER_LEAD_MINUTES = 45  # start this long before the charge needs to begin
PEAK_COVER_FROM_MINUTES = 12 * 60
PEAK_COVER_HOLD_FROM_MINUTES = 9 * 60  # a hold (not a top-up) may start this early (phase 59)
PEAK_COVER_PEAK_START_MINUTES = PEAK_START_MINUTES
PEAK_COVER_LAST_LOG_MINUTES = 15 * 60 + 30
PEAK_COVER_MIN_LOAD_HOURS = 2.0  # hours of 05:00-now data before the observed load is trusted
PEAK_COVER_LOAD_PERCENTILE = 75
PEAK_COVER_MIN_PEAK_DAYS = 3
PEAK_COVER_MIN_SPLIT_DAYS = 7  # same-day-type samples before the median drops the margin (phase 60)
DISCHARGE_EFFICIENCY = 0.92  # battery -> house; ~ CHARGE_EFFICIENCY, so round trip ~ ROUND_TRIP_EFFICIENCY


def peak_need_soc(
    peak_load_kwh: float, capacity_kwh: float,
    margin: float = PEAK_COVER_MARGIN, efficiency: float = DISCHARGE_EFFICIENCY,
) -> float:
    """SOC points the 16:00-19:00 house load takes out of the battery, with margin and discharge losses."""
    return peak_load_kwh * margin / efficiency / capacity_kwh * 100.0


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


INTRADAY_SCALE_FROM_HOUR = 10.0  # ratio ignored before this
INTRADAY_SCALE_FULL_HOUR = 11.0  # full weight from here (damped linearly in between)
INTRADAY_SCALE_MIN_FORECAST_KWH = 1.0
INTRADAY_SCALE_MIN, INTRADAY_SCALE_MAX = 0.5, 2.0


def intraday_forecast_scale(actual_so_far_kwh: float | None, forecast_so_far_kwh: float | None, hour: float) -> float | None:
    """Ratio of PV produced so far to the forecast for the same hours (phase 56); None when not trustworthy.

    Needs >= 1 kWh forecast so far and a real meter reading (missing is None, never 0). Clamped to
    0.5-2.0 and damped towards 1.0 between 10:00 and 11:00, when a few minutes of cloud swing the ratio.
    """
    if actual_so_far_kwh is None or forecast_so_far_kwh is None or hour < INTRADAY_SCALE_FROM_HOUR:
        return None
    if forecast_so_far_kwh < INTRADAY_SCALE_MIN_FORECAST_KWH or actual_so_far_kwh < 0:
        return None
    ratio = min(INTRADAY_SCALE_MAX, max(INTRADAY_SCALE_MIN, actual_so_far_kwh / forecast_so_far_kwh))
    weight = min(1.0, (hour - INTRADAY_SCALE_FROM_HOUR) / (INTRADAY_SCALE_FULL_HOUR - INTRADAY_SCALE_FROM_HOUR))
    return round(1.0 + weight * (ratio - 1.0), 3)


def scaled_solar_kwh(raw_kwh: float | None, scale: float | None) -> float | None:
    """Remaining solar after the intraday scale; the raw figure when there is no scale, None without a forecast."""
    if raw_kwh is None:
        return None
    return round(raw_kwh * scale, 3) if scale is not None else raw_kwh


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


def peak_load_estimate_split(
    samples: list[tuple[str, float]], default_kwh: float, weekend: bool
) -> tuple[float, str, float]:
    """(kWh, source, margin) for the 16:00-19:00 load, split by weekday/weekend (phase 60).

    ``samples`` are (ISO date, kWh). With >= PEAK_COVER_MIN_SPLIT_DAYS days of
    tonight's day type the median is used with no margin (the old p75 x 1.1
    stacked two layers of pessimism). Weekends keep the p75 and the margin: the
    evening load is known to be higher and spikier. Anything thinner falls back
    to ``peak_load_estimate_kwh`` (p75 of all days) with PEAK_COVER_MARGIN.
    """
    same: list[float] = []
    for day, kwh in samples:
        try:
            is_weekend = date.fromisoformat(str(day)).weekday() >= 5
        except ValueError:
            continue
        if is_weekend == weekend and kwh is not None and kwh >= 0:
            same.append(float(kwh))
    if not weekend and len(same) >= PEAK_COVER_MIN_SPLIT_DAYS:
        same.sort()
        mid = len(same) // 2
        median = same[mid] if len(same) % 2 else (same[mid - 1] + same[mid]) / 2.0
        return round(median, 2), "history_weekday_median", 1.0
    kwh, source = peak_load_estimate_kwh([k for _, k in samples], default_kwh)
    return kwh, source, PEAK_COVER_MARGIN


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
) -> dict[str, Any]:
    """Will the battery reach 16:00 with enough to run the peak window? If not, how to top up.

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
    required_soc = min(100, math.ceil(floor_soc + peak_need_soc(peak_load_kwh, capacity_kwh, margin)))
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


def state_fields_changed(current: Any, updates: dict[str, Any]) -> bool:
    """True when applying ``updates`` to ``current`` would change anything (phase 67).

    A dict/list/set that is the very same object counts as changed: it may have been mutated
    in place before being passed back. An unknown field is ignored, as update_state does."""
    for key, value in updates.items():
        if not hasattr(current, key):
            continue
        old = getattr(current, key)
        if old != value or (old is value and isinstance(value, (dict, list, set))):
            return True
    return False


def elapsed_seconds(later: datetime, earlier: datetime) -> float:
    """Real seconds from ``earlier`` to ``later``. Aware datetimes are compared in UTC: Python
    subtracts two datetimes that share a tzinfo as wall-clock time, which is 1 h out across a
    clock change. Naive values fall back to plain subtraction."""
    if later.tzinfo is None or earlier.tzinfo is None:
        return (later - earlier).total_seconds()
    return (later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)).total_seconds()


def shift_hours(moment: datetime, hours: float) -> datetime:
    """``moment`` moved by ``hours`` of real time (negative = earlier), DST-safe, same tzinfo."""
    if moment.tzinfo is None:
        return moment + timedelta(hours=hours)
    return (moment.astimezone(timezone.utc) + timedelta(hours=hours)).astimezone(moment.tzinfo)


def free_event_watch_from(free_start: datetime) -> datetime:
    """When the live sell-start watch begins (the longest sell window before the free start)."""
    return shift_hours(free_start, -FREE_EVENT_MAX_SELL_HOURS)


def free_event_in_watch(now: datetime, free_start: datetime, watch_from: datetime | None = None) -> bool:
    """True only from ``watch_from`` until the free start; no sell may begin outside it."""
    start = watch_from or free_event_watch_from(free_start)
    return elapsed_seconds(now, start) >= 0 and elapsed_seconds(free_start, now) > 0


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


# Attributes the dashboard reads: always present (None when unknown) so Spook
# never flags them as unknown.
FREE_EVENT_ATTRIBUTE_KEYS = (
    "free_start", "free_end", "sell_status", "sell_floor_soc", "expected_export_kwh", "expected_refill_kwh",
)


def free_event_attributes(event: dict) -> dict:
    """Sensor attributes: no raw meters, no sell estimates until a sell has run, plus ``sell_status``."""
    hidden = () if free_event_sold(event) else FREE_EVENT_ESTIMATE_KEYS
    attrs = dict.fromkeys(FREE_EVENT_ATTRIBUTE_KEYS)
    attrs.update({k: v for k, v in event.items() if not k.startswith("meters_") and k not in hidden})
    status = free_event_sell_status(event)
    if status:
        attrs["sell_status"] = status
    return attrs


def soc_trend_kw(samples: list[tuple[datetime, float]], capacity_kwh: float, now: datetime) -> float | None:
    """Battery charge rate (kW, negative = discharging) from SOC samples in the last
    FREE_EVENT_TREND_MINUTES; None until they span FREE_EVENT_TREND_MIN_MINUTES."""
    recent = [(ts, soc) for ts, soc in samples if elapsed_seconds(now, ts) <= FREE_EVENT_TREND_MINUTES * 60]
    if len(recent) < 2:
        return None
    (t0, s0), (t1, s1) = recent[0], recent[-1]
    hours = elapsed_seconds(t1, t0) / 3600
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
                      import_pence: float | None, capacity_kwh: float | None = None) -> dict[str, Any]:
    """Measured outcome of a finished free-electricity event (debug report).

    `sold_kwh` is grid export from the sell start to the free start; `free_import_kwh`
    is grid import during the free period, valued at what it would normally have
    cost (`import_pence`). No sell ran → `sold_kwh` 0.0. Missing inputs → None.
    `sold_from_battery_kwh` is the SOC fall over the sell (needs `capacity_kwh`);
    `sold_from_solar_kwh` is the rest of `sold_kwh` (never below 0).
    """
    free_import = meter_delta(event.get("meters_at_free_start"), meters_end, "grid_import")
    sold = (
        meter_delta(event.get("meters_at_sell_start"), event.get("meters_at_free_start"), "grid_export")
        if free_event_sold(event) else 0.0
    )
    from_battery = from_solar = None
    if free_event_sold(event):
        s0, s1 = event.get("soc_at_sell_start"), event.get("soc_at_free_start")
        if s0 is not None and s1 is not None and capacity_kwh:
            from_battery = round(max(0.0, (s0 - s1) / 100 * capacity_kwh), 2)
            if sold is not None:
                from_solar = round(max(0.0, sold - from_battery), 2)
    else:
        from_battery = from_solar = 0.0
    return {
        "sold_kwh": sold,
        "sold_from_battery_kwh": from_battery,
        "sold_from_solar_kwh": from_solar,
        "free_import_kwh": free_import,
        "free_import_value_gbp": _gbp(free_import, import_pence),
        "soc_at_end": soc_end,
    }


def event_report(record_type: str, phase: str, event: dict[str, Any]) -> dict[str, Any]:
    """Debug-channel JSON line for a free event lifecycle point (raw meter readings dropped,
    and the scheduling-time sell estimates unless a sell ran)."""
    hidden = () if free_event_sold(event) else FREE_EVENT_ESTIMATE_KEYS
    return {
        "type": record_type, "phase": phase,
        **{k: v for k, v in event.items() if not k.startswith("meters_") and k not in hidden},
    }


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
    free_credit: dict[str, tuple[float, float]] | None = None,
) -> dict[str, float | None]:
    """What 00:00-22:00 cost with the battery vs without it, from the meter snapshots.

    A free event (`free_credit`) costs nothing: its import is left out of the
    actual cost, and its house load out of the no-battery cost.

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
            _pence(load - direct - _credit(free_credit, start, 1), imp_price), _pence(solar - direct, exp_price),
            _pence(imp - _credit(free_credit, start), imp_price), _pence(exp, exp_price),
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


def free_event_kpi_credit(
    event: dict[str, Any] | None, day: date, capacity_kwh: float
) -> dict[str, tuple[float, float]] | None:
    """A finished free event's import and house load on `day`, per KPI segment start.

    Split by time overlap with the 00:00-22:00 segments. House load = import −
    battery gain over the free period (0 when the SOCs are missing). None when
    there's no finished event with a measured import that day.
    """
    event = event or {}
    free_import = event.get("free_import_kwh")
    if event.get("phase") != "done" or not _is_number(free_import) or free_import <= 0:
        return None
    start = datetime.fromisoformat(event["free_start"])
    end = datetime.fromisoformat(event["free_end"])
    if start.date() != day or end <= start:
        return None
    soc_a, soc_b = event.get("soc_at_free_start"), event.get("soc_at_end")
    gain = (soc_b - soc_a) / 100 * capacity_kwh if _is_number(soc_a) and _is_number(soc_b) else free_import
    load = max(0.0, free_import - gain)
    start_min = start.hour * 60 + start.minute
    end_min = start_min + (end - start).total_seconds() / 60
    credit = {}
    for seg_start, seg_end, _band, _sunny in _BASELINE_SEGMENTS:
        overlap = min(end_min, hhmm_to_minutes(seg_end)) - max(start_min, hhmm_to_minutes(seg_start))
        if overlap > 0:
            share = overlap / (end_min - start_min)
            credit[seg_start] = (free_import * share, load * share)
    return credit or None


def battery_value_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    """{"saving_gbp", "days"} over days with a `battery_saving_gbp`; saving None when none has one."""
    values = [d["battery_saving_gbp"] for d in days if isinstance(d.get("battery_saving_gbp"), (int, float))]
    return {"saving_gbp": round(sum(values), 2) if values else None, "days": len(values)}


# Battery and PV upgrade advisor (phase 50). Report only: never changes a setting.
UPGRADE_BATTERY_STEPS_KWH = (5.0, 10.0)
UPGRADE_PV_STEP_KWP = 2.0
UPGRADE_MIN_DAYS = 7
UPGRADE_HISTORY_DAYS = 90


def _day_flows(
    snapshots: dict[str, dict[str, Any]], battery_capacity_kwh: float
) -> list[tuple[str, str, float, float]] | None:
    """[(segment start, band, house load, solar)] per `_BASELINE_SEGMENTS`, as phase 37 derives them; None if incomplete."""
    flows = []
    for start, end, band, sunny in _BASELINE_SEGMENTS:
        load = _seg_value(snapshots, start, end, "load_kwh")
        imp = _seg_value(snapshots, start, end, "grid_import_kwh")
        exp = _seg_value(snapshots, start, end, "grid_export_kwh")
        if load is None or imp is None or exp is None:
            return None
        solar = 0.0
        if sunny:
            soc_a, soc_b = _snap(snapshots, start, "soc"), _snap(snapshots, end, "soc")
            if soc_a is None or soc_b is None:
                return None
            solar = max(0.0, load - imp + exp + (soc_b - soc_a) / 100 * battery_capacity_kwh)
        flows.append((start, band, load, solar))
    return flows


def simulate_system(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    sim_capacity_kwh: float,
    charge_kw: float,
    pv_scale: float = 1.0,
    wear_pence: float | None = None,
    floor_soc: float = BATTERY_LOW_SOC,
) -> dict[str, float | None]:
    """00:00-22:00 cost of one logged day re-run with a `sim_capacity_kwh` battery and PV × `pv_scale`.

    Load and solar per segment come from the meters as in phase 37
    (`battery_capacity_kwh` is the real battery, used only for that). The
    simulated battery starts the day at its floor, charges at 02:00-05:00
    (≤ charge_kw × 3 h) just enough for the later deficits it can serve more
    cheaply than the grid (perfect foresight: an upper bound for every size,
    so compare sizes, don't read the absolute), soaks up solar surplus and
    discharges into any deficit. Energy left at 22:00 is credited at the
    off-peak price it saves tomorrow. With a 0 kWh battery the cost equals
    phase 37's `no_battery_gbp`. Missing data → all None.
    """
    empty = {"cost_gbp": None, "import_kwh": None, "export_kwh": None, "cycled_kwh": None}
    flows = _day_flows(snapshots, battery_capacity_kwh)
    offpeak = prices_pence.get("offpeak")
    if flows is None or battery_capacity_kwh <= 0:
        return empty
    ce, de, wear = CHARGE_EFFICIENCY, DISCHARGE_EFFICIENCY, wear_pence or 0.0
    usable = max(0.0, sim_capacity_kwh) * (1 - floor_soc / 100)
    flows = [(s, b, load, solar * pv_scale) for s, b, load, solar in flows]

    def worth_it(band: str) -> bool:
        price = prices_pence.get(band)
        return offpeak is not None and price is not None and price > (offpeak + wear) / (ce * de)

    # Backward pass from 22:00: stored kWh needed at 05:00 to cover later deficits.
    need = 0.0
    for _start, band, load, solar in reversed(flows[2:]):
        deficit = max(0.0, load - solar) if worth_it(band) else 0.0
        need = max(0.0, need + deficit / de - max(0.0, solar - load) * ce)
    stored = cost = imported = exported = cycled = 0.0
    for start, band, load, solar in flows:
        imp_price, exp_price = prices_pence.get(band), price_at(export_bands, hhmm_to_minutes(start))
        grid_in = grid_out = 0.0
        if start == "02:00" and usable > 0:
            add = max(0.0, min(need, usable, charge_kw * 3 * ce) - stored)
            stored += add
            cycled += add / ce
            grid_in += add / ce
        surplus = solar - load
        if surplus >= 0:
            take = min(usable - stored, surplus * ce)
            stored += take
            cycled += take / ce
            grid_out += surplus - take / ce
        else:
            draw = min(stored, -surplus / de) if start != "02:00" else 0.0
            stored -= draw
            grid_in += -surplus - draw * de
        parts = (_pence(grid_in, imp_price), _pence(grid_out, exp_price))
        if any(p is None for p in parts):
            return empty
        cost += parts[0] - parts[1]
        imported += grid_in
        exported += grid_out
    if stored > 0:
        if offpeak is None:
            return empty
        cost -= stored / ce * offpeak
    cost += cycled * wear
    return {
        "cost_gbp": round(cost / 100, 2),
        "import_kwh": round(imported, 2),
        "export_kwh": round(exported, 2),
        "cycled_kwh": round(cycled, 2),
    }


def upgrade_scenarios(
    battery_capacity_kwh: float, pv_kwp: float | None
) -> list[dict[str, Any]]:
    """Scenarios to price: current, +5/+10 kWh of battery, and +2 kWp of PV when the installed kWp is known."""
    out = [{"name": "current", "capacity_kwh": battery_capacity_kwh, "pv_scale": 1.0}]
    for extra in UPGRADE_BATTERY_STEPS_KWH:
        out.append({"name": f"+{extra:g} kWh battery", "capacity_kwh": battery_capacity_kwh + extra,
                    "pv_scale": 1.0, "added_kwh": extra})
    if _is_number(pv_kwp) and pv_kwp > 0:
        out.append({"name": f"+{UPGRADE_PV_STEP_KWP:g} kWp PV", "capacity_kwh": battery_capacity_kwh,
                    "pv_scale": (pv_kwp + UPGRADE_PV_STEP_KWP) / pv_kwp, "added_kwp": UPGRADE_PV_STEP_KWP})
    return out


def upgrade_advisor(
    days: dict[str, dict[str, dict[str, Any]]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    charge_kw: float,
    wear_pence: float | None = None,
    pv_kwp: float | None = None,
    battery_price_per_kwh: float | None = None,
    pv_price_per_kwp: float | None = None,
) -> dict[str, Any]:
    """What +5/+10 kWh of battery (or +2 kWp of PV) would have saved over logged days, at today's prices.

    Each scenario's saving = simulated current cost − simulated scenario cost,
    on the same days and policy, so the model's bias largely cancels. Per
    month and in total; annualised = mean daily saving × 365 (says which months
    it covers: winter-only data understates PV). Payback in years when a
    price is given and the saving is positive; otherwise None. Fewer than
    `UPGRADE_MIN_DAYS` usable days → `"scenarios": []`.
    """
    scenarios = upgrade_scenarios(battery_capacity_kwh, pv_kwp)
    per_day: dict[str, list[float]] = {}
    used_dates: list[str] = []
    for day, snaps in sorted(days.items()):
        costs = [
            simulate_system(snaps, prices_pence, export_bands, battery_capacity_kwh, s["capacity_kwh"],
                            charge_kw, s["pv_scale"], wear_pence)["cost_gbp"]
            for s in scenarios
        ]
        if any(c is None for c in costs):
            continue
        used_dates.append(day)
        per_day[day] = costs
    months = sorted({d[:7] for d in used_dates})
    report: dict[str, Any] = {"days": len(used_dates), "months": months, "scenarios": []}
    if len(used_dates) < UPGRADE_MIN_DAYS:
        return report
    for i, s in enumerate(scenarios[1:], start=1):
        savings = {d: per_day[d][0] - per_day[d][i] for d in used_dates}
        total = sum(savings.values())
        by_month = {m: round(sum(v for d, v in savings.items() if d.startswith(m)), 2) for m in months}
        annual = total / len(used_dates) * 365
        if "added_kwh" in s:
            price = battery_price_per_kwh * s["added_kwh"] if _is_number(battery_price_per_kwh) else None
        else:
            price = pv_price_per_kwp * s["added_kwp"] if _is_number(pv_price_per_kwp) else None
        report["scenarios"].append({
            "name": s["name"],
            "saving_gbp": round(total, 2),
            "by_month_gbp": by_month,
            "annual_gbp": round(annual, 2),
            "price_gbp": round(price, 2) if price else None,
            "payback_years": round(price / annual, 1) if price and annual > 0 else None,
        })
    return report


def upgrade_summary(report: dict[str, Any]) -> str | None:
    """One line per scenario for the notification; None when there's nothing to say."""
    if not report.get("scenarios"):
        return None
    months = report["months"]
    span = months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}"
    lines = [f"Replayed {report['days']} logged days ({span}) at today's rates:"]
    for s in report["scenarios"]:
        payback = f"; payback ~{s['payback_years']:g} yrs at £{s['price_gbp']:,.0f}" if s["payback_years"] else ""
        lines.append(f"• {s['name']}: saved £{s['saving_gbp']:.2f} (~£{s['annual_gbp']:.0f}/yr){payback}")
    lines.append("Upper-bound estimate (perfect-foresight charging); data only covers the months listed.")
    return "\n".join(lines)


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


def bill_period(today: date, start_day: Any = 1) -> tuple[date, date, str]:
    """The latest complete bill period before ``today``: (first day, last day, label).

    Periods run from ``start_day`` (clamped 1-28) to the day before it next month. Day 1 is the
    previous calendar month, labelled ``YYYY-MM``; other days are labelled ``YYYY-MM-DD to YYYY-MM-DD``.
    """
    try:
        day = min(28, max(1, int(float(start_day))))
    except (TypeError, ValueError):
        day = 1

    def month_start(year: int, month: int) -> date:
        return date(year + (month - 1) // 12, (month - 1) % 12 + 1, day)

    next_start = month_start(today.year, today.month if today.day >= day else today.month - 1)
    start = month_start(next_start.year, next_start.month - 1)
    end = next_start - timedelta(days=1)
    label = start.strftime("%Y-%m") if day == 1 else f"{start.isoformat()} to {end.isoformat()}"
    return start, end, label


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


def meter_reconciliation(
    inverter_kwh: float | None,
    slots: Any,
    tolerance_pct: float = 8.0,
    min_diff_kwh: float = 0.5,
    through_slot: int = 44,
) -> dict[str, Any] | None:
    """Compare the inverter's grid kWh with the smart meter's half-hourly slots (phase 44).

    The inverter total is read at 22:00, so only the first ``through_slot`` slots
    (00:00-22:00) are summed. None when either side is missing or the slots are not
    48 numbers (rule 11: never guess). ``diverged`` needs both a % gap and an
    absolute gap, so a tiny day cannot trip it on rounding alone.
    """
    if inverter_kwh is None or not isinstance(slots, (list, tuple)) or len(slots) != HALF_HOURS_PER_DAY:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in slots):
        return None
    meter = float(sum(slots[:through_slot]))
    diff = float(inverter_kwh) - meter
    pct = diff / meter * 100.0 if meter >= 1.0 else None
    diverged = abs(diff) >= min_diff_kwh and (pct is None or abs(pct) >= tolerance_pct)
    return {
        "inverter_kwh": round(float(inverter_kwh), 2),
        "meter_kwh": round(meter, 2),
        "diff_kwh": round(diff, 2),
        "pct": None if pct is None else round(pct, 1),
        "diverged": diverged,
    }


def divergence_streak(history: list[dict[str, Any]]) -> int:
    """Length of the trailing run of consecutive-date diverged days in ``history``.

    ``history`` holds ``{"date": "YYYY-MM-DD", "diverged": bool}`` in any order; a
    gap in dates or a non-diverged day ends the run.
    """
    days = sorted(
        ((date.fromisoformat(str(h["date"])), bool(h.get("diverged"))) for h in history if h.get("date")),
        key=lambda x: x[0],
    )
    run = 0
    prev: date | None = None
    for d, bad in reversed(days):
        if prev is not None and (prev - d).days != 1:
            break
        if not bad:
            break
        run += 1
        prev = d
    return run


# Shadow go-live scorecard (phase 41): read-only, never enables a *_live option.
SCORECARD_WINDOW_DAYS = 30
SCORECARD_MIN_DAYS = 7
SCORECARD_MIN_CHANGED = 3
SCORECARD_HARM_GBP = 0.5
SCORECARD_SILENT_DAYS = 14
SCORECARD_ERROR_GAIN = 0.05  # weighted correction must beat the live factor by 5%
SCORECARD_ERROR_LOSS = 0.10  # ...and is harmful if 10% worse


def _in_window(records: list[dict[str, Any]], record_type: str, today: str, days: int) -> list[dict[str, Any]]:
    """``record_type`` records dated within the ``days`` ending ``today`` (ISO strings), one per date (last wins)."""
    try:
        floor = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for rec in records:
        d = rec.get("date")
        if rec.get("type") == record_type and isinstance(d, str) and floor < d <= today:
            by_date[d] = rec
    return [by_date[d] for d in sorted(by_date)]


def _score(days: int, changed: int, delta_gbp: float | None, dates: list[str], note: str = "") -> dict[str, Any]:
    """Verdict from record count, decisions changed and £ delta (positive = shadow would have helped).

    no_records: nothing logged. harmful: delta below -£0.50. ready: enough
    changed days with a positive delta. Otherwise not_yet (too few days, no
    £ estimate, or no change). Conservative by design.
    """
    if days == 0:
        verdict = "no_records"
    elif delta_gbp is not None and delta_gbp <= -SCORECARD_HARM_GBP:
        verdict = "harmful"
    elif days >= SCORECARD_MIN_DAYS and changed >= SCORECARD_MIN_CHANGED and delta_gbp is not None and delta_gbp > 0:
        verdict = "ready"
    else:
        verdict = "not_yet"
    return {
        "verdict": verdict, "days": days, "changed": changed, "delta_gbp": round_or_none(delta_gbp),
        "evidence": dates[-5:], "note": note,
    }


def _sum_or_none(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) if nums else None


def shadow_scorecard(
    records: list[dict[str, Any]], today: str, window_days: int = SCORECARD_WINDOW_DAYS
) -> dict[str, dict[str, Any]]:
    """Score each shadow feature against logged outcomes over the last ``window_days``.

    ``records`` are raw JSONL records. Returns {feature: {verdict, days,
    changed, delta_gbp, evidence, note}}; delta_gbp is None where no £
    counterfactual exists (never 0). A feature with no records in
    SCORECARD_SILENT_DAYS reports ``no_records`` so silent failures show.
    """
    def win(kind: str, days: int = window_days) -> list[dict[str, Any]]:
        return _in_window(records, kind, today, days)

    out: dict[str, dict[str, Any]] = {}
    plans = win("import_plan")
    # 15 import feedback: days the feedback would have moved the target.
    moved = [p for p in plans if p.get("import_feedback_adjustment") not in (None, 0)]
    out["import_feedback"] = _score(
        len([p for p in plans if "import_feedback_adjustment" in p]), len(moved), None,
        [p["date"] for p in moved], "no £ counterfactual; review the adjustment dates",
    )
    # 17 peak export: income minus replacement cost on days it would have exported.
    exports = win("peak_export")
    sold = [r for r in exports if r.get("decision") == "export"]
    out["peak_export"] = _score(
        len(exports),
        len(sold), _sum_or_none([r.get("gain_gbp") for r in sold]) if sold else None, [r["date"] for r in sold],
    )
    # 30 peak cover: day-rate top-up vs peak-rate purchase.
    covers = win("peak_cover")
    topped = [r for r in covers if r.get("decision") in ("top_up", "hold")]
    out["peak_cover"] = _score(
        len(covers),
        len(topped), _sum_or_none([r.get("gain_gbp") for r in topped]) if topped else None, [r["date"] for r in topped],
    )
    # 85% cap: positive extra cost is a loss, so the delta is its negative.
    caps = [k for k in win("day_kpis") if isinstance(k.get("soc_cap_shadow"), dict)]
    extra = _sum_or_none([k["soc_cap_shadow"].get("extra_cost_gbp") for k in caps])
    out["soc_cap_85"] = _score(
        len(caps), len([k for k in caps if (k["soc_cap_shadow"].get("extra_cost_gbp") or 0) > 0]),
        None if extra is None else -extra, [k["date"] for k in caps],
        "cap saves nothing modelled, so any extra cost counts against it",
    )
    # 20 weighted correction: mean absolute forecast error vs actual solar.
    actuals = {a["date"]: a for a in win("day_actuals")}
    live_err: list[float] = []
    weighted_err: list[float] = []
    dates: list[str] = []
    for p in plans:
        actual = (actuals.get(p["date"]) or {}).get("actual_solar_kwh")
        raw, live, weighted = p.get("raw_forecast_kwh"), p.get("forecast_correction_factor"), p.get("forecast_correction_weighted")
        if None in (actual, raw, live, weighted):
            continue
        live_err.append(abs(raw * live - actual))
        weighted_err.append(abs(raw * weighted - actual))
        dates.append(p["date"])
    score = _score(len(dates), 0, None, dates, "mean absolute error vs live factor")
    if len(dates) >= SCORECARD_MIN_DAYS and sum(live_err) > 0:
        ratio = sum(weighted_err) / sum(live_err)
        score["changed"] = len(dates)
        score["delta_gbp"] = None
        score["error_ratio"] = round(ratio, 3)
        score["verdict"] = "ready" if ratio <= 1 - SCORECARD_ERROR_GAIN else "harmful" if ratio >= 1 + SCORECARD_ERROR_LOSS else "not_yet"
    out["weighted_correction"] = score
    # 64 newsvendor ratio: asymmetric p-cost of the solar-scale error, live factor vs newsvendor ratio.
    nv_live: list[float] = []
    nv_shadow: list[float] = []
    nv_dates: list[str] = []
    for p in plans:
        actual = (actuals.get(p["date"]) or {}).get("actual_solar_kwh")
        raw, live = p.get("raw_forecast_kwh"), p.get("forecast_correction_factor")
        ratio, under, over = p.get("newsvendor_ratio"), p.get("newsvendor_under_p"), p.get("newsvendor_over_p")
        if None in (actual, raw, live, ratio, under, over) or raw <= 0.5:
            continue
        true = actual / raw
        # A scale above the true ratio under-buys (dear); below it over-buys.
        nv_live.append(under * max(0.0, live - true) + over * max(0.0, true - live))
        nv_shadow.append(under * max(0.0, ratio - true) + over * max(0.0, true - ratio))
        nv_dates.append(p["date"])
    nv_score = _score(len(nv_dates), 0, None, nv_dates, "asymmetric solar-scale error cost vs live factor")
    if len(nv_dates) >= SCORECARD_MIN_DAYS and sum(nv_live) > 0:
        nv_ratio = sum(nv_shadow) / sum(nv_live)
        nv_score["changed"] = len(nv_dates)
        nv_score["delta_gbp"] = None
        nv_score["error_ratio"] = round(nv_ratio, 3)
        nv_score["verdict"] = (
            "ready" if nv_ratio <= 1 - SCORECARD_ERROR_GAIN
            else "harmful" if nv_ratio >= 1 + SCORECARD_ERROR_LOSS else "not_yet"
        )
    out["newsvendor_target"] = nv_score
    # 23 gentle charge: nights it was applied (no £ counterfactual).
    gentle = [p for p in plans if p.get("gentle_charge_applied")]
    out["gentle_charge"] = _score(
        len([p for p in plans if "gentle_charge_applied" in p]), len(gentle), None, [p["date"] for p in gentle],
        "no £ counterfactual; check the charge-rate log",
    )
    # 39 multi-day: days where the shadow plan differed from the live plan.
    multi = win("multi_day_plan")
    differs = [m for m in multi if m.get("changes_target") or m.get("changes_peak")]
    out["multi_day"] = _score(len(multi), len(differs), None, [m["date"] for m in differs], "needs a ≥2 day low-solar run")
    # 34 capacity: latest estimate present and sampled.
    caps_est = win("capacity_estimate", 70)
    latest = caps_est[-1] if caps_est else {}
    est = latest.get("estimate_kwh")
    out["capacity"] = _score(len(caps_est), 0, None, [c["date"] for c in caps_est], "monthly estimate")
    if est is not None:
        out["capacity"]["verdict"] = "ready"
        out["capacity"]["estimate_kwh"] = est
        out["capacity"]["pct_of_configured"] = latest.get("pct_of_configured")
    # Silent-failure check: expected daily records absent for SCORECARD_SILENT_DAYS.
    for name, kind in (("peak_export", "peak_export"), ("peak_cover", "peak_cover")):
        if not win(kind, SCORECARD_SILENT_DAYS):
            out[name]["verdict"] = "no_records"
    return out


def scorecard_summary(card: dict[str, dict[str, Any]]) -> str:
    """One short human line per feature for the monthly notification."""
    lines = []
    for name, s in card.items():
        delta = "" if s.get("delta_gbp") is None else f", £{s['delta_gbp']:+.2f}"
        lines.append(f"{name}: {s['verdict'].replace('_', ' ')} ({s['changed']}/{s['days']} days{delta})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Phase 45: nightly data-integrity audit                                       #
# --------------------------------------------------------------------------- #

AUDIT_DAYS = 14
AUDIT_DAILY_TYPES = ("import_plan", "morning_state", "day_actuals", "day_kpis")
AUDIT_COST_SETTLE_DAYS = 2  # daily_cost can lag Octopus by a day or two; younger dates are not flagged
AUDIT_DEDUP_TYPES = AUDIT_DAILY_TYPES + ("charge_outcome", "peak_export", "peak_cover", "meter_snapshot")
AUDIT_SOLAR_MIN_KWH = 5.0  # a zero KPI against this much solar is a contradiction
_AUDIT_REDACT_PARTS = ("password", "username", "token", "secret", "key", "email", "plant_id", "serial", "account")


def audit_days(
    records: list[dict[str, Any]],
    today: date,
    days: int = AUDIT_DAYS,
    expect_cost: bool = False,
) -> list[dict[str, str]]:
    """Self-audit of the last `days` complete days of JSONL records. Pure; never rewrites anything.

    Returns issue dicts {kind, type, date, detail}, sorted by date then kind:
    - missing: a daily record type absent for a date (only dates from the first day with
      any record, so a fresh install is not flagged for history it never had; `daily_cost`
      only when `expect_cost` and the date is past the settle lag);
    - duplicate: a per-day record type logged twice for the same date (and time);
    - contradiction: self_sufficiency 0.0 against meaningful solar, or a 22:00 load of 0
      with solar produced (the zero-actuals guard should have kept it out).
    Today is excluded: its 22:00 records do not exist yet.
    """
    window = {(today - timedelta(days=n)).isoformat() for n in range(1, days + 1)}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    first_seen: str | None = None
    for rec in records:
        rtype, rdate = rec.get("type"), rec.get("date")
        if not rtype or not isinstance(rdate, str):
            continue
        by_key.setdefault((rtype, rdate), []).append(rec)
        if first_seen is None or rdate < first_seen:
            first_seen = rdate
    issues: list[dict[str, str]] = []
    if first_seen is None:
        return issues
    for d in sorted(window):
        if d < first_seen:
            continue
        for rtype in AUDIT_DAILY_TYPES:
            if (rtype, d) not in by_key:
                issues.append({"kind": "missing", "type": rtype, "date": d, "detail": f"no {rtype} record"})
        if expect_cost and d <= (today - timedelta(days=AUDIT_COST_SETTLE_DAYS)).isoformat():
            cost = by_key.get(("daily_cost", d))
            if not cost or not any(c.get("actual_import_cost_gbp") is not None for c in cost):
                issues.append({"kind": "missing", "type": "daily_cost", "date": d, "detail": "no settled import cost"})
        for rtype in AUDIT_DEDUP_TYPES:
            recs = by_key.get((rtype, d), [])
            times = [r.get("time") for r in recs]
            if len(times) != len(set(times)):
                issues.append({"kind": "duplicate", "type": rtype, "date": d, "detail": f"{len(recs)} records"})
        actuals = (by_key.get(("day_actuals", d)) or [{}])[0]
        kpis = (by_key.get(("day_kpis", d)) or [{}])[0]
        solar, load = _num(actuals.get("actual_solar_kwh")), _num(actuals.get("day_load_kwh"))
        if solar is not None and solar >= AUDIT_SOLAR_MIN_KWH:
            if _num(kpis.get("self_sufficiency_pct")) == 0:
                issues.append({
                    "kind": "contradiction", "type": "day_kpis", "date": d,
                    "detail": f"self-sufficiency 0% with {solar:g} kWh of solar",
                })
            if load == 0:
                issues.append({
                    "kind": "contradiction", "type": "day_actuals", "date": d,
                    "detail": f"day load 0 kWh with {solar:g} kWh of solar",
                })
    return sorted(issues, key=lambda i: (i["date"], i["kind"], i["type"]))


def audit_summary(issues: list[dict[str, str]]) -> dict[str, Any]:
    """Counts and per-class date lists for the sensor and Repairs issues."""
    classes: dict[str, list[str]] = {}
    for issue in issues:
        classes.setdefault(issue["kind"], []).append(f"{issue['date']} {issue['type']}")
    return {"count": len(issues), "classes": classes}


def redact_config(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of a config mapping with credentials and identifiers masked, for the diagnostics download."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _AUDIT_REDACT_PARTS):
            out[key] = "**REDACTED**"
        else:
            out[key] = _redact_value(value)
    return out


def _redact_value(value: Any) -> Any:
    """Recurse into mappings and lists/tuples so a secret nested inside a list is still masked."""
    if isinstance(value, Mapping):
        return redact_config(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def url_segment(value: Any) -> str:
    """A config id made safe for one URL path segment or query value ('/', '?', '&', '..' can't redirect the call)."""
    return quote(str(value).strip(), safe="")


# --------------------------------------------------------------------------- #
# Phase 47: KPI sensors                                                        #
# --------------------------------------------------------------------------- #

# sensor key → (day_kpis field, name, unit, kind); kind "money" is a daily £ total, else a measurement.
KPI_SENSORS = (
    ("kpi_avoidable_import", "avoidable_import_gbp", "Avoidable import today", "GBP", "money"),
    ("kpi_export_peak", "export_peak_gbp", "Peak export income today", "GBP", "money"),
    ("kpi_self_sufficiency", "self_sufficiency_pct", "Self-sufficiency today", "%", "measurement"),
    ("kpi_unused_charge", "unused_charge_kwh", "Unused charge today", "kWh", "measurement"),
    ("kpi_over_limit_import", "over_limit_import_kwh", "Over-limit import today", "kWh", "measurement"),
)


def kpi_sensor_values(record: dict[str, Any] | None) -> dict[str, float | None]:
    """Latest 22:00 day_kpis record → {sensor key: value}. Missing or non-numeric is None, never 0."""
    rec = record if isinstance(record, dict) else {}
    out: dict[str, float | None] = {}
    for key, field_name, _name, _unit, _kind in KPI_SENSORS:
        value = rec.get(field_name)
        out[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return out


# --------------------------------------------------------------------------- #
# Phase 48: grid outage detection                                              #
# --------------------------------------------------------------------------- #

OUTAGE_MIN_LOAD_KW = 0.05  # below this the runtime estimate is meaningless (battery idle)


def grid_online(value: Any) -> bool | None:
    """SolarSynkV3 `grid_status` → True (grid present), False (outage) or None (unknown).

    The exact format needs a live sample, so this is deliberately tolerant: words and 0/1.
    Anything unrecognised or unavailable is None, which is never treated as an outage.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "unknown", "unavailable", "none"):
        return None
    if text in ("0", "false", "off", "no") or any(w in text for w in ("disconnect", "offline", "off-grid", "off grid", "outage", "lost", "fail")):
        return False
    if text in ("1", "true", "on", "yes", "ok") or any(w in text for w in ("connect", "online", "normal", "present", "on-grid", "on grid", "ongrid", "grid-tied", "grid tied", "grid mode")):
        return True
    return None


GRID_ONLINE_MIN_V = 180.0  # at or above: mains present
GRID_OFFLINE_MAX_V = 100.0  # below: mains lost (between the two: unknown, no change)


def grid_online_from_voltage(voltage: Any) -> bool | None:
    """Grid present from the live grid phase voltage (live sample: `grid_status` reads 0 with 240 V, so it is unusable).

    None for missing/unparseable or the 100-180 V dead band, which never opens or closes an outage.
    """
    try:
        v = float(voltage)
    except (TypeError, ValueError):
        return None
    if v >= GRID_ONLINE_MIN_V:
        return True
    if v < GRID_OFFLINE_MAX_V:
        return False
    return None


def outage_runtime_hours(soc: float | None, shutdown_soc: float, capacity_kwh: float, load_kw: float | None) -> float | None:
    """Hours the battery can carry `load_kw` down to `shutdown_soc`; None when an input is missing or the load is ~0."""
    if soc is None or load_kw is None or capacity_kwh <= 0 or load_kw < OUTAGE_MIN_LOAD_KW:
        return None
    return round(max(0.0, soc - shutdown_soc) / 100 * capacity_kwh / load_kw, 1)


def outage_track(outage: dict[str, Any], soc: float | None) -> dict[str, Any]:
    """Active outage dict with the SOC low point updated (unchanged when SOC is unknown)."""
    if soc is None:
        return outage
    low = outage.get("soc_low")
    return {**outage, "soc_low": soc if low is None else min(low, soc)}


def outage_record(outage: dict[str, Any], end_iso: str, soc_end: float | None, capacity_kwh: float) -> dict[str, Any]:
    """Closed-outage log fields. kWh served = start SOC less the low point, as battery energy; None if unknown."""
    start_soc, low = outage.get("soc_start"), outage.get("soc_low")
    served = None
    if isinstance(start_soc, (int, float)) and isinstance(low, (int, float)):
        served = round(max(0.0, start_soc - low) / 100 * capacity_kwh, 2)
    minutes = None
    try:
        minutes = round((datetime.fromisoformat(end_iso) - datetime.fromisoformat(outage["start"])).total_seconds() / 60)
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "start": outage.get("start"), "end": end_iso, "minutes": minutes,
        "soc_start": start_soc, "soc_low": low, "soc_end": soc_end, "battery_kwh_served": served,
    }


# --------------------------------------------------------------------------- #
# Phase 49: PV string health (MPPT0 vs MPPT1 share)                            #
# --------------------------------------------------------------------------- #

STRING_SAMPLE_FROM_HOUR = 10   # like-for-like hours: the string split drifts with the sun's angle
STRING_SAMPLE_TO_HOUR = 15
STRING_MIN_TOTAL_KW = 1.0      # below this a "share" is noise
STRING_MIN_DAY_KWH = 1.0       # sampled kWh needed for a day to count
STRING_MIN_HISTORY = 7         # prior days needed before judging
STRING_MIN_DELTA = 0.08        # share must move at least this much (8 points) to count
STRING_MAD_K = 3.0
STRING_ALERT_STREAK = 3        # consecutive anomalous days before a notification


def string_share(mppt0_kwh: float | None, mppt1_kwh: float | None) -> float | None:
    """MPPT0 share of the sampled PV energy (0-1); None when either is unknown or the day was too thin."""
    if mppt0_kwh is None or mppt1_kwh is None:
        return None
    total = mppt0_kwh + mppt1_kwh
    return round(mppt0_kwh / total, 4) if total >= STRING_MIN_DAY_KWH else None


def string_ratio_anomaly(history: list[float], today: float | None) -> dict[str, Any] | None:
    """Compare today's MPPT0 share with the prior days' median; None until enough history.

    Anomalous when the move exceeds both STRING_MIN_DELTA and STRING_MAD_K scaled MADs
    (so a naturally steady split is judged tightly and a noisy one loosely).
    """
    vals = sorted(v for v in history if isinstance(v, (int, float)))
    if today is None or len(vals) < STRING_MIN_HISTORY:
        return None
    median = _median(vals)
    mad = _median(sorted(abs(v - median) for v in vals)) * 1.4826
    delta = today - median
    anomalous = abs(delta) >= max(STRING_MIN_DELTA, STRING_MAD_K * mad)
    return {
        "share": today, "median": round(median, 4), "delta": round(delta, 4), "anomalous": anomalous,
        "weaker": None if not anomalous else ("mppt0" if delta < 0 else "mppt1"),
    }


PV_STRING_ATTRIBUTE_KEYS = ("date", "median", "streak", "weaker", "mppt0_kwh", "mppt1_kwh")


def pv_string_attributes(pv_strings: Any) -> dict[str, Any]:
    """PV string sensor attributes: every key always present (None until the first 22:00 capture)."""
    data = pv_strings if isinstance(pv_strings, dict) else {}
    return {k: data.get(k) for k in PV_STRING_ATTRIBUTE_KEYS}


def string_streak(prev_streak: int, prev_weaker: str | None, result: dict[str, Any] | None) -> tuple[int, str | None]:
    """Consecutive anomalous days on the same weak string. A normal day resets; an unknown day holds."""
    if result is None:
        return prev_streak, prev_weaker
    weaker = result.get("weaker")
    if weaker is None:
        return 0, None
    return (prev_streak + 1 if weaker == prev_weaker else 1), weaker


# --------------------------------------------------------------------------- #
# Phase 46: one-night user overrides (service actions)                         #
# --------------------------------------------------------------------------- #

PLAN_RUN_MINUTES = 1 * 60 + 55  # the nightly plan runs at 01:55


def next_plan_date(now: datetime) -> date:
    """Date stamped on the next 01:55 plan: today before 01:55, else tomorrow."""
    return now.date() if now.hour * 60 + now.minute < PLAN_RUN_MINUTES else now.date() + timedelta(days=1)


def apply_user_override(
    target_soc: int, override: dict[str, Any] | None, plan_date: str, soc: float | None
) -> tuple[int, str | None]:
    """Apply a one-night override after every other cap. Returns (target, "set"/"skip"/None).

    Only an override dated for this plan applies. "set" clamps to the discharge floor..100.
    "skip" holds the battery where it is (no overnight grid charge); it needs a known SOC,
    otherwise the plan is left alone rather than guessing.
    """
    if not isinstance(override, dict) or override.get("date") != plan_date:
        return target_soc, None
    if override.get("skip"):
        if soc is None:
            return target_soc, None
        return max(BATTERY_BOTTOM_SOC, min(target_soc, int(soc))), "skip"
    wanted = override.get("soc")
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool):
        return max(BATTERY_BOTTOM_SOC, min(100, int(round(wanted)))), "set"
    return target_soc, None


# --------------------------------------------------------------------------- #
# Phase 43: inverter alarm/event feed feasibility probe                        #
# --------------------------------------------------------------------------- #

EVENT_PROBE_ROUTES = (
    # Round 2 (04/11/2026): plant/{id}/events accepts `lan` but returns code 500 with only page/limit/lan,
    # so round 3 adds the date range and ids a typical events DTO would want.
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&startTime={start}&endTime={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&startDate={start}&endDate={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&beginTime={start}&endTime={today}&status=1",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&sn={sn}&startTime={start}&endTime={today}&type=1",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&date={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&plantId={plant_id}&startTime={start}&endTime={today}",
)


def event_probe_paths(inverter_sn: str, plant_id: str, today: str = "2026-01-01", start: str = "2025-12-01") -> list[str]:
    """Candidate read-only GET routes for the cloud's alarm/event list, with ids and dates filled in."""
    return [r.format(sn=url_segment(inverter_sn), plant_id=url_segment(plant_id), today=today, start=start) for r in EVENT_PROBE_ROUTES]


_EVENT_TEXT_KEYS = ("eventName", "name", "content", "message", "desc", "description", "alarmName", "title")
_EVENT_CODE_KEYS = ("code", "eventCode", "alarmCode", "errorCode", "id")
_EVENT_TIME_KEYS = ("startTime", "time", "occurTime", "createTime", "happenTime")
_EVENT_CATEGORY_WORDS = (
    ("battery", ("batt", "bms", "cell", "soc")),
    ("grid", ("grid", "mains", "ac ", "utility", "fac", "vac")),
    ("charge", ("charg", "overload", "over temp", "temperature", "fan")),
)


def _first_text(item: dict, keys) -> str | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def normalise_inverter_events(raw) -> list[dict] | None:
    """Tolerant parse of the Sunsynk realtime event list (phase 43). The healthy reply is an empty list.

    The shape of a live fault is not yet known, so each item is reduced to text/code/time/category with
    the raw item kept; None when the reply is not a list (never 0 or []: unknown is not 'no faults')."""
    if not isinstance(raw, list):
        return None
    events: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            item = {"content": str(item)}
        text = _first_text(item, _EVENT_TEXT_KEYS)
        code = _first_text(item, _EVENT_CODE_KEYS)
        when = _first_text(item, _EVENT_TIME_KEYS)
        lowered = (text or "").lower()
        category = next((c for c, words in _EVENT_CATEGORY_WORDS if any(w in lowered for w in words)), "other")
        events.append(
            {"key": f"{code}|{text}|{when}", "text": text, "code": code, "time": when, "category": category, "raw": item}
        )
    return events


def new_inverter_events(events: list[dict], seen: list[str]) -> list[dict]:
    """Events whose key has not been reported before (dedup across checks)."""
    known = set(seen or [])
    return [e for e in events if e["key"] not in known]


# Cloud notices feed (messageTd/messages), captured 04/11/2026: messageType 7 = grid lost, 2 = grid restored.
_GRID_NOTICE_TYPES = {7: "lost", 2: "restored"}


def _grid_notice_kind(record: dict) -> str | None:
    try:
        kind = _GRID_NOTICE_TYPES.get(int(record.get("messageType")))
    except (TypeError, ValueError):
        kind = None
    if kind:
        return kind
    text = str(record.get("description") or "").lower()
    if "disconnected from the grid" in text:
        return "lost"
    if "grid has been restored" in text:
        return "restored"
    return None


def normalise_grid_notices(records, inverter_sn: str | None = None) -> list[dict] | None:
    """Grid lost/restored notices from the Sunsynk cloud feed, oldest first (phase 43 follow-up).

    Keeps only records whose kind is known and whose `sn` matches (when both are set). None when the
    reply is not a list, so an unreadable feed is never read as 'no outages'."""
    if not isinstance(records, list):
        return None
    notices = []
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = _grid_notice_kind(record)
        if kind is None or record.get("id") in (None, ""):
            continue
        sn = record.get("sn")
        if inverter_sn and sn and str(sn) != str(inverter_sn):
            continue
        notices.append({"id": str(record["id"]), "kind": kind, "time": record.get("createAt"), "sn": sn})
    return sorted(notices, key=lambda n: str(n["time"] or ""))


def new_grid_notices(notices: list[dict], seen: list[str]) -> list[dict]:
    """Notices whose id has not been logged before."""
    known = set(seen or [])
    return [n for n in notices if n["id"] not in known]


_COUNT_KEYS = ("fault", "warning", "offline", "protect", "normal", "total")


def normalise_inverter_counts(data) -> dict | None:
    """Account-wide inverter status counts (`/api/v1/inverters/count`); None if unreadable.

    Missing or non-numeric keys stay None (never 0)."""
    if not isinstance(data, dict):
        return None
    counts = {}
    for key in _COUNT_KEYS:
        try:
            counts[key] = int(data[key]) if data.get(key) is not None else None
        except (TypeError, ValueError):
            counts[key] = None
    return counts if any(v is not None for v in counts.values()) else None


def inverter_fault_flag(active_events, counts, health_faults=None) -> bool | None:
    """True when the realtime list has events, the cloud counts a fault/protect state, or a register
    fault word is set (health_faults: bool | None from health_fault_flag).

    False only when at least one source was read and shows nothing; None when none was read."""
    signals = [] if health_faults is None else [bool(health_faults)]
    if active_events is not None:
        signals.append(bool(active_events))
    if counts:
        known = [counts.get(k) for k in ("fault", "protect") if counts.get(k) is not None]
        if known:
            signals.append(any(v > 0 for v in known))
    return any(signals) if signals else None


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


def ledger_point(date: str, health) -> dict | None:
    """The compact per-day history entry kept in state for the trend."""
    if not isinstance(health, dict) or health.get("soh_pct") is None:
        return None
    return {
        "date": date, "soh_pct": health["soh_pct"], "cycles_max": health.get("cycles_max"),
        "discharge_total_kwh": health.get("discharge_total_kwh"),
    }


PEAK_COVER_STARTUP_RETRIES = 5  # 60 s apart, like the initial plan's wait for the first sensor poll


def peak_cover_startup_retry_due(minutes: int, soc, outage_active: bool, attempts: int) -> bool:
    """Re-try peak cover a minute later after a restart (the startup plan wiped its Flux 1 slot).

    Only inside the peak-cover window (09:00-16:00), only while SOC is not yet readable or a grid
    outage is still flagged, and at most PEAK_COVER_STARTUP_RETRIES times."""
    in_window = PEAK_COVER_HOLD_FROM_MINUTES <= minutes < PEAK_COVER_PEAK_START_MINUTES
    return in_window and (soc is None or outage_active) and attempts < PEAK_COVER_STARTUP_RETRIES



# Receding-horizon MPC planner (phase 69). Shadow only: logged next to the rule-based plan, never pushed.
MPC_SLOT_MINUTES = 30
MPC_MAX_SLOTS = 48  # 24 h ahead
MPC_EVENING_FROM_MINUTES = 16 * 60
MPC_OVERNIGHT_FROM_MINUTES = 22 * 60
MPC_DAY_FROM_MINUTES = 6 * 60


def mpc_load_kw(minute: int, loads: dict[str, float]) -> float:
    """House load kW at a minute of day: overnight 22:00-06:00, evening 16:00-22:00, else day."""
    minute %= 1440
    if minute >= MPC_OVERNIGHT_FROM_MINUTES or minute < MPC_DAY_FROM_MINUTES:
        return loads["overnight"]
    if minute >= MPC_EVENING_FROM_MINUTES:
        return loads["evening"]
    return loads["day"]


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


# Self-learned probabilistic solar forecast (phase 70). Shadow: never replaces min(raw, corrected).
FORECAST_QUANTILE_LEVELS = (0.1, 0.5, 0.9)
FORECAST_QUANTILE_MIN_DAYS = 14
FORECAST_QUANTILE_HISTORY_DAYS = 395  # the 13-month log
FORECAST_QUANTILE_RATIO_CAP = (0.05, 5.0)


def _season(month: int) -> set[int]:
    """The month and its two neighbours (Dec wraps to Jan)."""
    return {(month - 2) % 12 + 1, month, month % 12 + 1}


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of sorted ``values``."""
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def forecast_ratio_samples(paired_days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{date, ratio, band, month}] of actual / raw forecast; raw ≤ 0.5 kWh or no actual skipped."""
    out = []
    for d in paired_days:
        raw, actual = d.get("raw_forecast_kwh"), d.get("actual_solar_kwh")
        if not _is_number(raw) or raw <= 0.5 or not _is_number(actual) or actual < 0:
            continue
        try:
            month = date.fromisoformat(str(d.get("date"))).month
        except ValueError:
            continue
        ratio = max(FORECAST_QUANTILE_RATIO_CAP[0], min(FORECAST_QUANTILE_RATIO_CAP[1], actual / raw))
        out.append({"date": d["date"], "ratio": ratio, "band": d.get("forecast_band"), "month": month})
    return out


def forecast_quantiles(
    samples: list[dict[str, Any]],
    month: int,
    band: str | None,
    raw_forecast_kwh: float | None,
    min_days: int = FORECAST_QUANTILE_MIN_DAYS,
) -> dict[str, Any] | None:
    """P10/P50/P90 of actual/raw from the narrowest bin with enough days.

    Bins, narrowest first: forecast band × season (month ± 1), season, band,
    all. kWh = raw × ratio when a raw forecast is given. None below
    ``min_days`` in every bin (thin history is not a guess).
    """
    season = _season(month)
    bins = (
        ("band_season", lambda s: s["band"] == band and s["month"] in season),
        ("season", lambda s: s["month"] in season),
        ("band", lambda s: s["band"] == band),
        ("all", lambda s: True),
    )
    for name, keep in bins:
        if band is None and name.startswith("band"):
            continue
        ratios = sorted(s["ratio"] for s in samples if keep(s))
        if len(ratios) < min_days:
            continue
        out: dict[str, Any] = {"bin": name, "days": len(ratios)}
        for q in FORECAST_QUANTILE_LEVELS:
            key = f"p{int(q * 100)}"
            ratio = round(_quantile(ratios, q), 3)
            out[f"{key}_ratio"] = ratio
            out[f"{key}_kwh"] = round(raw_forecast_kwh * ratio, 2) if _is_number(raw_forecast_kwh) else None
        return out
    return None


def forecast_quantile_coverage(
    samples: list[dict[str, Any]], min_days: int = FORECAST_QUANTILE_MIN_DAYS
) -> dict[str, Any]:
    """Walk-forward calibration: share of days whose actual fell below P10, inside P10-P90, above P90.

    Each day is scored against quantiles from earlier days only. A calibrated
    band has ~10% / 80% / 10%. Shares are None until a day can be scored.
    """
    ordered = sorted(samples, key=lambda s: s["date"])
    below = inside = above = 0
    for i, s in enumerate(ordered):
        q = forecast_quantiles(ordered[:i], s["month"], s["band"], None, min_days)
        if q is None:
            continue
        if s["ratio"] < q["p10_ratio"]:
            below += 1
        elif s["ratio"] > q["p90_ratio"]:
            above += 1
        else:
            inside += 1
    n = below + inside + above
    share = (lambda k: round(k / n, 2) if n else None)
    return {"days": n, "below_p10": share(below), "inside": share(inside), "above_p90": share(above)}


def scale_hourly(hourly: dict[int, float] | None, ratio: float | None) -> dict[int, float] | None:
    """An hourly forecast × ``ratio`` (a learned quantile); unchanged when either is missing."""
    if hourly is None or not _is_number(ratio):
        return hourly
    return {h: kwh * ratio for h, kwh in hourly.items()}


# Half-hourly household load forecaster (phase 71). Shadow unless load_profile_live.
LOAD_PROFILE_WINDOW_DAYS = 28
LOAD_PROFILE_MIN_DAYS = 7
LOAD_PROFILE_IMPORT_SHARE = 0.9  # import ≥ 90% of a segment's load → it's the shape (battery/solar idle)
HEATING_BASE_C = 15.5
HEATING_MIN_DAYS = 14
HEATING_SCALE_BOUNDS = (0.7, 1.5)
# (snapshot start, snapshot end, first slot, slot count); 22:00-24:00 has no snapshot.
_PROFILE_SEGMENTS = (
    ("00:00", "02:00", 0, 4), ("02:00", "05:00", 4, 6), ("05:00", "16:00", 10, 22),
    ("16:00", "19:00", 32, 6), ("19:00", "22:00", 38, 6),
)


def day_load_slots(
    snapshots: dict[str, dict[str, Any]], import_slots: list[float] | None = None
) -> list[float] | None:
    """48 half-hour house-load kWh for one logged day.

    Each segment's metered load (SolarSynk snapshots) is spread over its
    half-hours: in the shape of the smart-meter import when import covered
    ≥90% of that load (the battery and solar were idle, so import ≈ load),
    otherwise evenly. 22:00-24:00 repeats the 19:00-22:00 rate. Any missing
    segment → None.
    """
    hh = import_slots if isinstance(import_slots, list) and len(import_slots) == 48 else None
    out = [0.0] * 48
    for start, end, first, count in _PROFILE_SEGMENTS:
        load = _seg_value(snapshots, start, end, "load_kwh")
        if load is None:
            return None
        weights = [1.0 / count] * count
        if hh is not None:
            seg = [max(0.0, float(v)) if _is_number(v) else 0.0 for v in hh[first:first + count]]
            total = sum(seg)
            if total > 0 and total >= LOAD_PROFILE_IMPORT_SHARE * load:
                weights = [v / total for v in seg]
        for i, w in enumerate(weights):
            out[first + i] = load * w
    rate = sum(out[38:44]) / 6
    out[44:48] = [rate] * 4
    return out


def heating_scale(points: list[tuple[float, float]], temp_now: float | None) -> float | None:
    """Load multiplier for today's temperature from (daily load kWh, mean °C) history.

    Least squares of load on heating degrees max(0, 15.5 − °C); applied only
    with ≥14 points, a positive slope and some spread in heating degrees.
    Scale = fitted load now ÷ fitted load at the history's mean, clamped
    0.7-1.5. None when it can't be fitted (no temperature effect assumed).
    """
    if temp_now is None or len(points) < HEATING_MIN_DAYS:
        return None
    xs = [max(0.0, HEATING_BASE_C - t) for _, t in points]
    ys = [load for load, _ in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var < 1.0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    if slope <= 0 or my <= 0:
        return None
    now = my + slope * (max(0.0, HEATING_BASE_C - temp_now) - mx)
    return round(max(HEATING_SCALE_BOUNDS[0], min(HEATING_SCALE_BOUNDS[1], now / my)), 3)


def load_profile(
    snapshot_days: dict[str, dict[str, dict[str, Any]]],
    half_hourly_import: dict[str, list[float]],
    today: date,
    weekend: bool,
    skip_dates: set[str] | frozenset[str] = frozenset(),
    temps: dict[str, float] | None = None,
    temp_now: float | None = None,
    window_days: int = LOAD_PROFILE_WINDOW_DAYS,
    min_days: int = LOAD_PROFILE_MIN_DAYS,
) -> dict[str, Any] | None:
    """Per-slot median house load for today's day type, temperature-scaled; None below ``min_days``.

    Uses days in the window before today (away days skipped) of the same
    day type when there are ``min_days`` of them, else all. Summary bands:
    peak 16:00-19:00 kWh, evening kW over 19:00-02:00 (wrapping into the
    early slots), day kW over 05:00-16:00.
    """
    days: list[tuple[list[float], bool, str]] = []
    for day_str, snaps in snapshot_days.items():
        if day_str in skip_dates:
            continue
        try:
            day = date.fromisoformat(day_str)
        except (TypeError, ValueError):
            continue
        if not 0 < (today - day).days <= window_days:
            continue
        slots = day_load_slots(snaps, half_hourly_import.get(day_str))
        if slots is not None:
            days.append((slots, day.weekday() >= 5, day_str))
    matching = [d for d in days if d[1] == weekend]
    day_type = ("weekend" if weekend else "weekday") if len(matching) >= min_days else "all"
    if day_type != "all":
        days = matching
    if len(days) < min_days:
        return None
    profile = [_median([d[0][i] for d in days]) for i in range(48)]
    points = [(sum(d[0]), temps[d[2]]) for d in days if temps and _is_number(temps.get(d[2]))]
    scale = heating_scale(points, temp_now)
    if scale is not None:
        profile = [v * scale for v in profile]
    return {
        "slots": [round(v, 3) for v in profile],
        "days": len(days),
        "day_type": day_type,
        "temp_scale": scale,
        "total_kwh": round(sum(profile), 2),
        "peak_kwh": round(sum(profile[32:38]), 2),
        "evening_kw": round((sum(profile[38:48]) + sum(profile[0:4])) / EVENING_RESERVE_HOURS, 3),
        "day_kw": round(sum(profile[10:32]) / 11.0, 3),
    }


def load_profile_backtest(
    snapshot_days: dict[str, dict[str, dict[str, Any]]],
    half_hourly_import: dict[str, list[float]],
) -> dict[str, Any]:
    """Walk-forward 16:00-19:00 load error: the profile vs the phase 60 day-type median, per day.

    Each day is predicted from earlier days only. MAE in kWh for both;
    None until a day can be scored by both.
    """
    errors: dict[str, list[float]] = {"profile": [], "median": []}
    for day_str in sorted(snapshot_days):
        actual = _snap_delta(snapshot_days[day_str], "16:00", "19:00", "load_kwh")
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            continue
        if actual is None:
            continue
        prior = {d: s for d, s in snapshot_days.items() if d < day_str}
        profile = load_profile(prior, half_hourly_import, day, day.weekday() >= 5)
        samples = [(d, v) for d, s in prior.items() if (v := _snap_delta(s, "16:00", "19:00", "load_kwh")) is not None]
        if profile is None or not samples:
            continue
        median, _source, _margin = peak_load_estimate_split(samples[-14:], 0.0, day.weekday() >= 5)
        errors["profile"].append(abs(profile["peak_kwh"] - actual))
        errors["median"].append(abs(median - actual))
    n = len(errors["profile"])
    mae = (lambda k: round(sum(errors[k]) / n, 3) if n else None)
    return {"days": n, "profile_mae_kwh": mae("profile"), "median_mae_kwh": mae("median")}


# Dynamic-tariff native mode (phase 76). Shadow only: the MPC re-run on Agile half-hourly prices
# next to the Flux plan; logged, never pushed. Control stays on Flux.
AGILE_SPIKE_PENCE = 35.0  # a slot at or above this is a price spike worth flagging
DYNAMIC_SHADOW_MINUTE = 22 * 60 + 5  # after tomorrow's Agile prices (~16:00) and the 22:00 snapshot


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


# Flexibility-market export events (phase 77). Shadow only: what the battery could safely offer
# a paid export event, logged; nothing is exported.
FLEX_DEFAULT_HOURS = 1.0  # an on/off entity with no end time
FLEX_MAX_HOURS = 4.0


def flex_event_window(attrs: Any, now: datetime, tz: Any = None) -> tuple[str, float]:
    """(event start HH:MM, hours left) from a calendar entity's start_time/end_time attributes.

    An on/off entity has neither: it starts now and lasts FLEX_DEFAULT_HOURS.
    Hours are capped at FLEX_MAX_HOURS and never below half an hour.
    """
    attrs = attrs if isinstance(attrs, dict) else {}
    tz = tz or now.tzinfo
    start = _as_aware(attrs.get("start_time"), tz)
    end = _as_aware(attrs.get("end_time"), tz)
    start_local = (start.astimezone(tz) if tz is not None else start) if start is not None else now
    hours = (end - now).total_seconds() / 3600 if end is not None else FLEX_DEFAULT_HOURS
    hours = max(0.5, min(FLEX_MAX_HOURS, hours))
    return f"{start_local.hour:02d}:{start_local.minute:02d}", round(hours, 2)


def flex_event_offer(
    soc: float | None,
    reserve_soc: int,
    battery_capacity_kwh: float,
    hours: float,
    house_load_kw: float,
    price_pence: float | None,
    offpeak_pence: float | None,
    export_rate_kw: float,
    wear_pence: float | None = None,
    slots_held: bool = False,
) -> dict[str, Any]:
    """kWh the battery could sell into a paid export event without breaking the evening reserve.

    Reuses the peak export floor (`peak_export_plan`): reserve + the house
    load over the event + margin, and the same worth-it test against the
    off-peak refill cost after round-trip losses and wear. The offer is also
    capped by the export rate over the event. `decision` is offer /
    no_surplus / not_worth_it / no_price / no_soc / slots_held (a free event
    owns the slots).
    """
    empty = {"offer_kwh": 0.0, "value_gbp": None, "gain_gbp": None, "floor_soc": None}
    if slots_held:
        return {"decision": "slots_held", **empty}
    if not _is_number(soc):
        return {"decision": "no_soc", **empty}
    need_soc = max(0.0, house_load_kw) * hours / battery_capacity_kwh * 100 if battery_capacity_kwh > 0 else 0.0
    plan = peak_export_plan(
        soc, reserve_soc, battery_capacity_kwh, price_pence, offpeak_pence, wear_pence=wear_pence, peak_need_soc=need_soc
    )
    decision = {"export": "offer"}.get(plan["decision"], plan["decision"])
    if decision != "offer":
        return {"decision": decision, **empty, "floor_soc": plan["target_soc"]}
    offer = min(plan["export_kwh"], max(0.0, export_rate_kw) * hours)
    if offer < PEAK_EXPORT_MIN_KWH:
        return {"decision": "no_surplus", **empty, "floor_soc": plan["target_soc"]}
    value = offer * price_pence / 100
    gain = value - offer * (offpeak_pence / ROUND_TRIP_EFFICIENCY + (wear_pence or 0.0)) / 100
    return {
        "decision": "offer",
        "offer_kwh": round(offer, 2),
        "value_gbp": round(value, 2),
        "gain_gbp": round(gain, 2),
        "floor_soc": plan["target_soc"],
    }


# Household electrification and sizing advisor (phase 78). Report only: extends the phase 50
# upgrade report with a heat pump on Cosy vs gas, and a higher inverter limit (clipped solar).
HEAT_PUMP_COP_RANGE = (2.0, 4.5)
HEAT_PUMP_COP_AT_0C = 2.8
HEAT_PUMP_COP_PER_C = 0.08
HEAT_PUMP_COP_DEFAULT = 2.8  # a day with no logged outside temperature
BOILER_EFFICIENCY = 0.9
ELECTRIFY_MIN_DAYS = 14
ELECTRIFY_HISTORY_DAYS = 395  # the 13-month log


def heat_pump_cop(temp_c: float | None) -> float:
    """Seasonal air-source COP at a daily mean outside temperature (linear, clamped); a default without one."""
    if not _is_number(temp_c):
        return HEAT_PUMP_COP_DEFAULT
    low, high = HEAT_PUMP_COP_RANGE
    return round(max(low, min(high, HEAT_PUMP_COP_AT_0C + HEAT_PUMP_COP_PER_C * temp_c)), 2)


def mean_band_price(bands: list[tuple[int, int, float]]) -> float | None:
    """Time-weighted mean p/kWh over the 48 half-hours of a day; None if any half-hour has no price."""
    prices = [band_price(bands, i * 30) for i in range(HALF_HOURS_PER_DAY)]
    if not prices or any(p is None for p in prices):
        return None
    return round(sum(prices) / len(prices), 2)


def _annual_block(savings: dict[str, float], price_gbp: float | None) -> dict[str, Any]:
    total = sum(savings.values())
    annual = total / len(savings) * 365 if savings else 0.0
    return {
        "days": len(savings),
        "months": sorted({d[:7] for d in savings}),
        "saving_gbp": round(total, 2),
        "annual_gbp": round(annual, 2),
        "price_gbp": round(price_gbp, 2) if _is_number(price_gbp) and price_gbp > 0 else None,
        "payback_years": round(price_gbp / annual, 1) if _is_number(price_gbp) and price_gbp > 0 and annual > 0 else None,
    }


def heat_pump_scenario(
    days: list[dict[str, Any]],
    gas_unit_pence: float | None,
    hp_price_pence: float | None,
    gas_standing_pence: float | None = None,
    install_price_gbp: float | None = None,
    temps: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """What replacing gas with an air-source heat pump would have saved on the logged days.

    Per day: gas kWh = (gas cost − standing) ÷ unit price; heat = gas kWh ×
    boiler efficiency; heat-pump kWh = heat ÷ COP(the day's outside °C);
    saving = gas cost (standing included: the gas supply is assumed capped
    off) − heat-pump kWh × `hp_price_pence` (the Cosy all-day mean). Away
    days and days without a gas cost are skipped. None without a gas unit
    price, a heat-pump price, or `ELECTRIFY_MIN_DAYS` usable days.
    Confidence is always "low": COP, boiler efficiency and the heating share
    of gas (hot water and cooking are counted as heat) are assumptions.
    """
    if not (_is_number(gas_unit_pence) and gas_unit_pence > 0 and _is_number(hp_price_pence)):
        return None
    standing = gas_standing_pence if _is_number(gas_standing_pence) else 0.0
    temps = temps or {}
    savings: dict[str, float] = {}
    gas_kwh = hp_kwh = 0.0
    cops: list[float] = []
    for day in days:
        date_str, cost = day.get("date"), day.get("actual_gas_cost_gbp")
        if not date_str or day.get("away") or not _is_number(cost):
            continue
        kwh = max(0.0, cost * 100 - standing) / gas_unit_pence
        cop = heat_pump_cop(temps.get(date_str))
        elec = kwh * BOILER_EFFICIENCY / cop
        savings[date_str] = (cost * 100 - elec * hp_price_pence) / 100
        gas_kwh += kwh
        hp_kwh += elec
        cops.append(cop)
    if len(savings) < ELECTRIFY_MIN_DAYS:
        return None
    return {
        **_annual_block(savings, install_price_gbp),
        "gas_kwh": round(gas_kwh, 1),
        "heat_pump_kwh": round(hp_kwh, 1),
        "mean_cop": round(sum(cops) / len(cops), 2),
        "hp_price_p": hp_price_pence,
        "confidence": "low",
        "assumptions": f"COP {HEAT_PUMP_COP_RANGE[0]:g}–{HEAT_PUMP_COP_RANGE[1]:g} by outside temp, "
                       f"boiler {BOILER_EFFICIENCY:.0%}, all gas treated as heat, gas standing charge dropped, "
                       "Cosy all-day mean price (no shifting into cheap windows)",
    }


def export_limit_scenario(
    days: list[dict[str, Any]], export_pence: float | None, price_gbp: float | None = None
) -> dict[str, Any] | None:
    """What a higher inverter limit would have earned: the logged clipped solar sold at the midday export price.

    Clipping is estimated from the PV string power near the limit (phase 29),
    so treat it as indicative ("medium" confidence). None without an export
    price or `ELECTRIFY_MIN_DAYS` days with a clipping figure.
    """
    if not _is_number(export_pence):
        return None
    savings = {
        d["date"]: d["clipped_solar_kwh"] * export_pence / 100
        for d in days if d.get("date") and _is_number(d.get("clipped_solar_kwh"))
    }
    if len(savings) < ELECTRIFY_MIN_DAYS:
        return None
    return {
        **_annual_block(savings, price_gbp),
        "clipped_kwh": round(sum(d["clipped_solar_kwh"] for d in days if d.get("date") in savings), 2),
        "export_p": export_pence,
        "confidence": "medium",
    }


def electrification_summary(report: dict[str, Any]) -> list[str]:
    """Notification lines for the heat-pump and inverter-limit scenarios (empty when neither ran)."""
    lines = []
    hp = report.get("heat_pump")
    if hp:
        payback = f"; payback ~{hp['payback_years']:g} yrs" if hp.get("payback_years") else ""
        if hp["saving_gbp"] >= 0:
            outcome = f"saved £{hp['saving_gbp']:.2f} over {hp['days']} days (~£{hp['annual_gbp']:.0f}/yr"
        else:
            outcome = f"cost £{-hp['saving_gbp']:.2f} more over {hp['days']} days (~£{-hp['annual_gbp']:.0f}/yr more"
        lines.append(
            f"• Heat pump on Cosy instead of gas: {outcome} at the logged season's rate{payback}; "
            f"low confidence: {hp['assumptions']})"
        )
    limit = report.get("export_limit")
    if limit:
        lines.append(
            f"• Higher inverter limit: {limit['clipped_kwh']:g} kWh clipped solar would have earned "
            f"£{limit['saving_gbp']:.2f} (~£{limit['annual_gbp']:.0f}/yr)"
        )
    return lines


def upgrade_options(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every priced option in an upgrade advisor report as flat rows, best annual £ first (dashboard).

    Rows: {name, annual_gbp, saving_gbp, payback_years, days, confidence}. Battery/PV
    scenarios, the heat pump (phase 78) and the inverter limit; missing ones are skipped.
    """
    if not isinstance(report, dict):
        return []
    rows = [
        {"name": s.get("name"), "annual_gbp": s.get("annual_gbp"), "saving_gbp": s.get("saving_gbp"),
         "payback_years": s.get("payback_years"), "days": report.get("days"), "confidence": "upper bound"}
        for s in report.get("scenarios") or [] if isinstance(s, dict)
    ]
    for key, name in (("heat_pump", "Heat pump (Cosy) vs gas"), ("export_limit", "Higher inverter limit")):
        block = report.get(key)
        if isinstance(block, dict):
            rows.append({"name": name, "annual_gbp": block.get("annual_gbp"), "saving_gbp": block.get("saving_gbp"),
                         "payback_years": block.get("payback_years"), "days": block.get("days"),
                         "confidence": block.get("confidence")})
    rows = [r for r in rows if _is_number(r["annual_gbp"])]
    return sorted(rows, key=lambda r: -r["annual_gbp"])
