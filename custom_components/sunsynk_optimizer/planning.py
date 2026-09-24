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


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


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


def net_cost_gbp(import_cost: float | None, export_income: float | None, gas_cost: float | None) -> float | None:
    """import - export + gas, or None if any input is missing (never treat missing as £0)."""
    if import_cost is None or export_income is None or gas_cost is None:
        return None
    return round(import_cost - export_income + gas_cost, 2)


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def sum_field(days: list[dict[str, Any]], key: str) -> float | None:
    """Sum `key` across days that have it, rounded to 2dp; None if no day does."""
    values = [d[key] for d in days if d.get(key) is not None]
    return round(sum(values), 2) if values else None
