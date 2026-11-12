# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Learned loads, load profile, baseload, spikes, away detection."""

from __future__ import annotations

from datetime import date
from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    EVENING_RESERVE_HOURS,
    MPC_DAY_FROM_MINUTES,
    PEAK_COVER_MARGIN,
    _is_number,
    _median,
    _seg_value,
    _snap_delta,
    median,
    percentile,
)


# Phase 12: overnight load rate learned from the 06:00 morning_state reading
# (SolarSynkV3 daily load at 06:00 = 00:00-06:00 household use).
OVERNIGHT_LOAD_HOURS = 6.0

LEARNED_LOAD_WINDOW_DAYS = 28

LEARNED_LOAD_MIN_DAYS = 7

LEARNED_LOAD_CLAMP = (0.5, 2.0)

# Phase 111: a cold snap lifts the load faster than a 28-night median follows.
LEARNED_LOAD_RECENT_DAYS = 3

LEARNED_LOAD_RECENT_MIN = 2


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

    Phase 111: the result is never below the mean of the last
    ``LEARNED_LOAD_RECENT_DAYS`` nights (same day type when split, at
    least ``LEARNED_LOAD_RECENT_MIN`` of them), so it can only raise the rate.
    """
    rates: list[tuple[float, bool]] = []
    recent: list[tuple[float, bool]] = []
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
        if (today - day).days <= LEARNED_LOAD_RECENT_DAYS:
            recent.append(rates[-1])
    if weekend is not None:
        matching = [r for r in rates if r[1] == weekend]
        if len(matching) >= min_days:
            rates = matching
            recent = [r for r in recent if r[1] == weekend]
    if len(rates) < min_days:
        return None, len(rates)
    learned = _median([r for r, _ in rates])
    if len(recent) >= LEARNED_LOAD_RECENT_MIN:
        learned = max(learned, sum(r for r, _ in recent) / len(recent))
    return round(learned, 3), len(rates)


def resolve_load_kw(config_kw: float, learned_kw: float | None) -> tuple[float, str]:
    """Return (rate used, source): learned, clamped to 0.5x-2x config, else config."""
    if learned_kw is None:
        return config_kw, "config"
    low, high = LEARNED_LOAD_CLAMP
    return round(max(config_kw * low, min(config_kw * high, learned_kw)), 3), "learned"


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


PEAK_COVER_MIN_LOAD_HOURS = 2.0  # hours of 05:00-now data before the observed load is trusted

PEAK_COVER_LOAD_PERCENTILE = 75

PEAK_COVER_MIN_PEAK_DAYS = 3

PEAK_COVER_MIN_SPLIT_DAYS = 7  # same-day-type samples before the median drops the margin (phase 60)


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
    return round(percentile(values, PEAK_COVER_LOAD_PERCENTILE), 2), "history"


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
        return round(median(same), 2), "history_weekday_median", 1.0
    kwh, source = peak_load_estimate_kwh([k for _, k in samples], default_kwh)
    return kwh, source, PEAK_COVER_MARGIN


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

DAYTIME_IMPORT_CHECKS = 2

DAYTIME_IMPORT_SOC_MARGIN = 5


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


def hourly_profile(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-hour {hour, soc, grid_import_kwh, load_kwh} from timed samples of daily meters.

    ``samples`` are {minute, soc, grid_import_kwh, load_kwh} (the two kWh values are
    cumulative day counters). The change between consecutive samples belongs to the earlier
    sample's hour; a missing value or a counter reset (negative step) contributes nothing, and an
    hour with no usable step reports None, never 0 (rule 11). soc is the hour's first reading.
    """
    ordered = sorted((s for s in samples if _is_number(s.get("minute"))), key=lambda s: s["minute"])
    hours: dict[int, dict[str, Any]] = {}
    for i, cur in enumerate(ordered):
        hour = int(cur["minute"]) // 60
        row = hours.setdefault(hour, {"hour": hour, "soc": cur.get("soc"), "grid_import_kwh": None, "load_kwh": None})
        if row["soc"] is None:
            row["soc"] = cur.get("soc")
        if i + 1 == len(ordered):
            continue
        nxt = ordered[i + 1]
        for key in ("grid_import_kwh", "load_kwh"):
            a, b = cur.get(key), nxt.get(key)
            if _is_number(a) and _is_number(b) and b >= a:
                row[key] = round((row[key] or 0.0) + b - a, 3)
    return [hours[h] for h in sorted(hours)]


MPC_EVENING_FROM_MINUTES = 16 * 60

MPC_OVERNIGHT_FROM_MINUTES = 22 * 60


def mpc_load_kw(minute: int, loads: dict[str, float]) -> float:
    """House load kW at a minute of day: overnight 22:00-06:00, evening 16:00-22:00, else day."""
    minute %= 1440
    if minute >= MPC_OVERNIGHT_FROM_MINUTES or minute < MPC_DAY_FROM_MINUTES:
        return loads["overnight"]
    if minute >= MPC_EVENING_FROM_MINUTES:
        return loads["evening"]
    return loads["day"]


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
