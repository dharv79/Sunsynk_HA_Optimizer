# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Overnight import plan: target SOC tree, charge rate, Flux 1 sizing, gentle charging, drain/nudge feedback, full-charge day."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .core import (
    BATTERY_BOTTOM_SOC,
    BATTERY_LOW_SOC,
    CHARGE_EFFICIENCY,
    PEAK_COVER_PEAK_START_MINUTES,
    PEAK_END_MINUTES,
    PEAK_START_MINUTES,
    _as_float,
    _median,
    _num,
    _percentile,
    band_key_at,
    hhmm_to_minutes,
    price_at,
)


# Below this (pessimistic) daily forecast the plan switches to max import:
# 95-100% target and the full 02:00-05:00 window.
LOW_SOLAR_THRESHOLD_KWH = 7.0

# Between the threshold and this forecast the bridge target is blended toward 100%
# (linear), so 7.19 kWh does not drop from 100% to a bare-minimum bridge.
LOW_SOLAR_BLEND_END_KWH = 10.0

# The simple bridge sizes to the learned average load; real mornings are spikier.
BRIDGE_LOAD_MARGIN = 1.25

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


def blend_toward_full(decision: "TargetDecision", forecast_kwh: float) -> "TargetDecision":
    """Raise a bridge target toward 100% as the forecast falls from the blend end to the low-solar line."""
    if not (LOW_SOLAR_THRESHOLD_KWH <= forecast_kwh < LOW_SOLAR_BLEND_END_KWH) or decision.target_soc >= 100:
        return decision
    frac = (LOW_SOLAR_BLEND_END_KWH - forecast_kwh) / (LOW_SOLAR_BLEND_END_KWH - LOW_SOLAR_THRESHOLD_KWH)
    blended = min(100, round(decision.target_soc + (100 - decision.target_soc) * frac))
    if blended <= decision.target_soc:
        return decision
    decision.target_soc, decision.reason = blended, "solar_bridge_blend"
    return decision


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
        return blend_toward_full(TargetDecision(
            bridge_soc(gap, battery_capacity_kwh), "solar_bridge_hourly",
            hourly_forecast_used=True, bridge_hour=bridge_hour,
        ), low_solar_forecast_kwh)

    if hours_to_solar is not None:
        # Simple bridge assumes solar covers load the instant the sun is up; the
        # synthetic ramp tops it up on slow-ramp mornings.
        decision = TargetDecision(
            bridge_soc(hours_to_solar * avg_consumption_kw * BRIDGE_LOAD_MARGIN, battery_capacity_kwh),
            "solar_bridge",
        )
        if synthetic_profile is not None:
            gap, decision.bridge_hour = walk_bridge_gap(synthetic_profile, avg_consumption_kw)
            ramp = bridge_soc(gap, battery_capacity_kwh)
            if ramp > decision.target_soc:
                decision.target_soc = ramp
                decision.reason = "solar_bridge_ramp"
                decision.synthetic_ramp = True
        return blend_toward_full(decision, low_solar_forecast_kwh)

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
    quarter_slots = math.ceil(raw_minutes / 15 - 1e-9)
    return max(2 * 60 + 15, min(5 * 60, 2 * 60 + quarter_slots * 15))


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


GENTLE_CHARGE_MARGIN = 1.2

GENTLE_CHARGE_MIN_CURRENT_A = 10


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
    end_min = hhmm_to_minutes(flux1_end)
    if end_min is None:
        return drain_0600
    window = 6.0 - end_min / 60.0
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
    end_min = hhmm_to_minutes(flux1_end)
    if end_min is None:
        return None
    hours = max(0.0, 5.0 - end_min / 60.0)
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
    elif reason in ("solar_bridge_hourly", "solar_bridge", "solar_bridge_ramp", "solar_bridge_blend"):
        hour = plan.get("bridge_hour")
        until = f" until solar covers the house around {int(hour):02d}:00" if isinstance(hour, int) else " until solar takes over"
        extra = {
            "solar_bridge_ramp": " (raised for a slow-ramp morning)",
            "solar_bridge_blend": f" (raised toward 100% because the {low_txt} forecast is only just above the {LOW_SOLAR_THRESHOLD_KWH:.0f} kWh line)",
        }.get(reason, "")
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


# Phase 15: overnight-target feedback from real day-rate import (shadow first).
IMPORT_FEEDBACK_WINDOW_DAYS = 14

IMPORT_FEEDBACK_MIN_DAYS = 5

IMPORT_FEEDBACK_TOLERANCE_KWH = 0.3

IMPORT_FEEDBACK_UNUSED_KWH = 0.5

IMPORT_FEEDBACK_STEP = 5


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


CLIPPED_EARLY_FULL_HOUR = 11


def overnight_target_too_high(clipped_kwh: float | None, first_full_hour: float | None) -> bool | None:
    """Flag a day that clipped after the battery was already full before 11:00 (target too high).

    None when there is nothing to judge (no clipping data or never seen full).
    """
    if clipped_kwh is None or first_full_hour is None:
        return None
    return clipped_kwh > 0 and first_full_hour < CLIPPED_EARLY_FULL_HOUR


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


def carry_held_flux1(payload: dict[str, Any], hold_flux1: dict[str, Any] | None, minutes: int) -> dict[str, Any]:
    """Keep an active peak-cover hold when a push carries only Flux 2 (phase 90).

    A push rebuilds every slot it does not name from the config baseline, so a
    Flux 2 only push (trim, export pause) would otherwise wipe the Flux 1 hold.
    The hold ends at 16:00; a push that names Flux 1 is left as it is.
    """
    if payload.get("flux_1") or not hold_flux1 or minutes >= PEAK_COVER_PEAK_START_MINUTES:
        return payload
    return {**payload, "flux_1": dict(hold_flux1)}


GRID_CURRENT_DRIFT_TOLERANCE_A = 1.0

_FLUX_TIME_FIELDS = ("startTime", "endTime")


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
# Multi-day low-solar planning (phase 39, shadow)                              #
# --------------------------------------------------------------------------- #

MULTI_DAY_MIN_RUN = 2


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
