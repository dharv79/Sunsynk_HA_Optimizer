# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Open-Meteo solar forecast with a learned sun and shading model (phase 136, shadow). Pure.

Open-Meteo gives the hourly sunlight on the panels (global tilted irradiance, W/m²) for
each string's tilt and direction. ``GTI / 1000 x kWp`` is the reference kWh at a
performance ratio of 1. Two learned factors turn that into a forecast for this roof:

- shade: per sun-position bin (azimuth x elevation), the clear-hour ratio of actual to
  reference, relative to the clear-hour ratio overall. Below 1 where trees or houses shade
  the panels at that sun position; 1 where there is too little data.
- weather ratio: per cloud bucket (clear / mixed / overcast), actual over shaded reference.
  It carries the system's losses and Open-Meteo's own bias in each kind of weather.

Hours are local clock hours; ``hour`` h covers h:00-h+1:00.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from math import atan2, cos, degrees, pi, radians, sin, sqrt
from typing import Any

from .core import _num, minutes_to_hhmm, round_or_none
from .forecast import cloud_bucket

OM_DEFAULT_RATIO = 0.8  # performance ratio before anything is learned
OM_HISTORY_DAYS = 120  # forecast/actual days the model learns from
OM_MIN_REF_KWH = 0.05  # an hour with less forecast sunlight than this teaches nothing
OM_SUN_BIN_AZ_DEG = 15
OM_SUN_BIN_EL_DEG = 5
OM_SHADE_MIN_HOURS = 3  # clear hours a sun bin needs before its shade factor counts
OM_RATIO_MIN_HOURS = 6  # hours a weather ratio needs before it replaces the overall one
OM_CLEAR_CLOUD_PCT = 30  # an hour at or below this cloud cover counts as clear for shading
OM_SHADE_RANGE = (0.1, 1.2)
OM_RATIO_RANGE = (0.05, 2.0)
OM_START_LATEST_HOUR = 15  # a "solar start" after this is no morning start at all
CLOUD_DAYLIGHT_HOURS = (8, 16)  # hours averaged for cloud cover when sunrise/sunset are unknown
OM_BACKFILL_DAYS = 365  # phase 138: how far back the archive backfill reaches
OM_BACKFILL_MIN_DAYS = 14  # startup backfills once when the model has fewer days than this
OM_ARCHIVE_START = date(2024, 1, 2)  # Previous Runs API archive starts January 2024


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def compass_to_open_meteo(azimuth_deg: float) -> float:
    """Compass direction (0 N, 90 E, 180 S, 270 W) to Open-Meteo's (0 S, -90 E, 90 W)."""
    return float(azimuth_deg) % 360.0 - 180.0


def om_strings(
    kwp: Any, tilt: Any, azimuth: Any, tilt2: Any = None, azimuth2: Any = None, share2_pct: Any = None,
) -> list[tuple[float, float, float]]:
    """[(tilt °, compass azimuth °, kWp)] per panel string; [] (feature off) without kWp, tilt or
    direction. A second string needs both its tilt and direction and takes ``share2_pct`` of kWp."""
    total, t1, a1 = _num(kwp), _num(tilt), _num(azimuth)
    if total is None or total <= 0 or t1 is None or a1 is None:
        return []
    t2, a2, share = _num(tilt2), _num(azimuth2), _num(share2_pct)
    if t2 is None or a2 is None:
        return [(t1, a1, total)]
    part = _clamp(share if share is not None else 50.0, (0.0, 100.0)) / 100.0
    return [(t1, a1, round(total * (1 - part), 3)), (t2, a2, round(total * part, 3))]


def sun_position(lat: float, lon: float, when: datetime) -> tuple[float, float]:
    """(elevation °, compass azimuth °) of the sun at an aware ``when`` (NOAA approximation, ±1°)."""
    utc = when.astimezone(timezone.utc)
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    g = 2 * pi / 365.0 * (utc.timetuple().tm_yday - 1 + (hour - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * cos(g) - 0.032077 * sin(g) - 0.014615 * cos(2 * g) - 0.040849 * sin(2 * g))
    decl = (0.006918 - 0.399912 * cos(g) + 0.070257 * sin(g) - 0.006758 * cos(2 * g) + 0.000907 * sin(2 * g)
            - 0.002697 * cos(3 * g) + 0.00148 * sin(3 * g))
    solar_minutes = hour * 60 + eqtime + 4 * lon
    ha = radians(solar_minutes / 4.0 - 180.0)
    phi = radians(lat)
    sin_el = sin(phi) * sin(decl) + cos(phi) * cos(decl) * cos(ha)
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = degrees(atan2(sin_el, sqrt(max(0.0, 1 - sin_el * sin_el))))
    # Azimuth from south, positive west; +180 makes it a compass bearing.
    azimuth = degrees(atan2(sin(ha), cos(ha) * sin(phi) - (sin(decl) / cos(decl)) * cos(phi))) + 180.0
    return round(elevation, 2), round(azimuth % 360.0, 2)


def sun_bin(elevation: float, azimuth: float) -> str:
    """Sun-position bin key "az/el" (15° of azimuth by 5° of elevation)."""
    az = int(azimuth // OM_SUN_BIN_AZ_DEG) * OM_SUN_BIN_AZ_DEG
    el = int(max(0.0, elevation) // OM_SUN_BIN_EL_DEG) * OM_SUN_BIN_EL_DEG
    return f"{az}/{el}"


def _mid_hour(day: date, hour: int, tz: tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 30, tzinfo=tz)


def om_reference_hours(
    times_utc: list[str], gti_by_string: list[list[Any]], kwp_by_string: list[float], day: date, tz: tzinfo,
) -> dict[int, float]:
    """{local hour: reference kWh} for ``day`` from Open-Meteo hourly GTI per string.

    ``times_utc`` are Open-Meteo's naive UTC "YYYY-MM-DDTHH:MM" stamps; each GTI value is the
    mean over the hour ending at its stamp, so it belongs to the local hour that starts one hour
    earlier. An hour with any string's value missing is left out (never read as 0).
    """
    out: dict[int, float] = {}
    for i, stamp in enumerate(times_utc):
        try:
            end = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        start = (end - timedelta(hours=1)).astimezone(tz)
        if start.date() != day:
            continue
        total = 0.0
        for series, kwp in zip(gti_by_string, kwp_by_string):
            value = _num(series[i]) if i < len(series) else None
            if value is None:
                total = None
                break
            total += max(0.0, value) / 1000.0 * kwp
        if total is not None:
            out[start.hour] = round(out.get(start.hour, 0.0) + total, 4)
    return out


def om_cloud_hours(times_utc: list[str], cloud: list[Any], day: date, tz: tzinfo) -> dict[int, float]:
    """{local hour: cloud %} for ``day``: the mean of the readings at the hour's start and end."""
    at: dict[datetime, float] = {}
    for stamp, value in zip(times_utc, cloud):
        v = _num(value)
        try:
            when = datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc).astimezone(tz)
        except ValueError:
            continue
        if v is not None:
            at[when.replace(minute=0, second=0, microsecond=0)] = v
    out: dict[int, float] = {}
    for when, value in at.items():
        if when.date() != day:
            continue
        nxt = at.get(when + timedelta(hours=1))
        out[when.hour] = round(value if nxt is None else (value + nxt) / 2, 1)
    return out


def om_training_hours(
    forecasts: list[dict[str, Any]], profiles: list[dict[str, Any]], lat: float, lon: float, tz: tzinfo, today: date,
    history_days: int = OM_HISTORY_DAYS,
) -> list[dict[str, Any]]:
    """Joined {date, hour, ref_kwh, pv_kwh, cloud_pct, bin} hours from logged ``om_forecast`` and
    ``hourly_profile`` records before ``today``. Hours without a measured pv_kwh, or with too little
    forecast sunlight to teach anything, are left out."""
    floor = (today - timedelta(days=history_days)).isoformat()
    actual: dict[tuple[str, int], float] = {}
    for rec in profiles:
        d = rec.get("date")
        if not isinstance(d, str):
            continue
        for row in rec.get("hourly") or []:
            pv = _num(row.get("pv_kwh"))
            if pv is not None and isinstance(row.get("hour"), int):
                actual[(d, row["hour"])] = pv
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in sorted(forecasts, key=lambda r: str(r.get("recorded_at") or ""), reverse=True):
        d = rec.get("date")
        if not isinstance(d, str) or not floor <= d < today.isoformat() or d in seen:
            continue
        seen.add(d)
        day = date.fromisoformat(d)
        for row in rec.get("hours") or []:
            hour, ref = row.get("hour"), _num(row.get("ref_kwh"))
            pv = actual.get((d, hour)) if isinstance(hour, int) else None
            if ref is None or pv is None or ref < OM_MIN_REF_KWH:
                continue
            el, az = sun_position(lat, lon, _mid_hour(day, hour, tz))
            out.append({"date": d, "hour": hour, "ref_kwh": ref, "pv_kwh": pv,
                        "cloud_pct": _num(row.get("cloud_pct")), "bin": sun_bin(el, az)})
    return out


def om_learn(hours: list[dict[str, Any]], shade_hours: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Shade per sun bin and a weather ratio per cloud bucket from joined training hours.

    ``shade_hours`` (phase 138: backfilled hours older than the learning window) add to the
    shading only; sun bins are seasonal by nature, the weather ratios stay on recent hours.
    """
    clear = [
        h for h in [*hours, *(shade_hours or [])]
        if h["cloud_pct"] is not None and h["cloud_pct"] <= OM_CLEAR_CLOUD_PCT
    ]
    clear_ref = sum(h["ref_kwh"] for h in clear)
    clear_ratio = sum(h["pv_kwh"] for h in clear) / clear_ref if len(clear) >= OM_RATIO_MIN_HOURS and clear_ref > 0 else None
    shade: dict[str, float] = {}
    if clear_ratio:
        bins: dict[str, list[dict[str, Any]]] = {}
        for h in clear:
            bins.setdefault(h["bin"], []).append(h)
        for key, group in bins.items():
            ref = sum(h["ref_kwh"] for h in group)
            if len(group) >= OM_SHADE_MIN_HOURS and ref > 0:
                shade[key] = round(_clamp(sum(h["pv_kwh"] for h in group) / ref / clear_ratio, OM_SHADE_RANGE), 3)

    def _ratio(group: list[dict[str, Any]]) -> float | None:
        ref = sum(h["ref_kwh"] * shade.get(h["bin"], 1.0) for h in group)
        if len(group) < OM_RATIO_MIN_HOURS or ref <= 0:
            return None
        return round(_clamp(sum(h["pv_kwh"] for h in group) / ref, OM_RATIO_RANGE), 3)

    overall = _ratio(hours)
    ratios: dict[str, float] = {}
    for bucket in ("clear", "mixed", "overcast"):
        r = _ratio([h for h in hours if cloud_bucket(h["cloud_pct"]) == bucket])
        if r is not None:
            ratios[bucket] = r
    return {
        "shade": shade, "ratios": ratios, "ratio_all": overall,
        "days": len({h["date"] for h in hours}), "hours": len(hours),
    }


def om_predict(
    ref_hours: dict[int, float], cloud_hours: dict[int, float], model: dict[str, Any] | None,
    lat: float, lon: float, day: date, tz: tzinfo,
) -> dict[str, Any]:
    """Today's forecast: {kwh, hourly {hour: kWh}, basis}. basis is "learned" once an overall
    ratio exists, else "default" (performance ratio OM_DEFAULT_RATIO, no shading)."""
    model = model or {}
    overall = model.get("ratio_all")
    basis = "learned" if overall else "default"
    hourly: dict[int, float] = {}
    for hour, ref in sorted(ref_hours.items()):
        el, az = sun_position(lat, lon, _mid_hour(day, hour, tz))
        if el <= 0 or ref <= 0:
            hourly[hour] = 0.0
            continue
        ratio = (model.get("ratios") or {}).get(cloud_bucket(cloud_hours.get(hour)), overall) or OM_DEFAULT_RATIO
        hourly[hour] = round(ref * (model.get("shade") or {}).get(sun_bin(el, az), 1.0) * ratio, 3)
    return {"kwh": round(sum(hourly.values()), 2) if hourly else None, "hourly": hourly, "basis": basis}


def om_solar_start(hourly_kwh: dict[int, float], load_kw: float | None) -> str | None:
    """"HH:MM" when forecast solar first covers the house load, interpolated between hour mid-points;
    None when the load is unknown or solar never covers it before OM_START_LATEST_HOUR."""
    load = _num(load_kw)
    if load is None or load <= 0:
        return None
    prev = None
    for hour in range(0, OM_START_LATEST_HOUR + 1):
        value = hourly_kwh.get(hour, 0.0)
        if value >= load:
            if prev is None or prev >= value:
                return minutes_to_hhmm(hour * 60)
            frac = (load - prev) / (value - prev)
            return minutes_to_hhmm(int(round(((hour - 0.5) + frac) * 60)))
        prev = value
    return None


def daylight_cloud_cover(items: list[dict[str, Any]], day: date, tz: tzinfo, first_hour: int, last_hour: int) -> float | None:
    """Mean cloud_coverage % over ``day``'s hours first_hour..last_hour from an hourly weather
    forecast (items with an ISO ``datetime``); None when no hour in range has a reading."""
    values: list[float] = []
    for item in items:
        try:
            when = datetime.fromisoformat(str(item.get("datetime") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        local = when.astimezone(tz)
        v = _num(item.get("cloud_coverage"))
        if v is not None and local.date() == day and first_hour <= local.hour <= last_hour:
            values.append(v)
    return round_or_none(sum(values) / len(values), 1) if values else None


def hourly_counter_kwh(samples: list[dict[str, Any]], key: str, max_gap_minutes: int = 90) -> dict[int, float]:
    """{hour: kWh} from a cumulative day counter sampled at odd minutes, by interpolating the
    counter at each hour boundary. A boundary not bracketed by two samples within
    ``max_gap_minutes`` (or bracketing a counter reset) is unknown, and so is every hour touching it."""
    pts = sorted(
        ((int(s["minute"]), float(s[key])) for s in samples
         if _num(s.get("minute")) is not None and _num(s.get(key)) is not None),
        key=lambda p: p[0],
    )
    at: dict[int, float] = {}
    for (m0, v0), (m1, v1) in zip(pts, pts[1:]):
        if m1 - m0 > max_gap_minutes or v1 < v0:
            continue
        for boundary in range(-(-m0 // 60) * 60, m1 + 1, 60):
            at[boundary] = v0 if m1 == m0 else v0 + (v1 - v0) * (boundary - m0) / (m1 - m0)
    for m, v in pts:
        if m % 60 == 0:
            at.setdefault(m, v)
    out: dict[int, float] = {}
    for boundary, value in at.items():
        nxt = at.get(boundary + 60)
        if nxt is not None and nxt >= value:
            out[boundary // 60] = round(nxt - value, 3)
    return out


def om_archive_days(
    times_utc: list[str], gti_by_string: list[list[Any]], kwp_by_string: list[float], cloud: list[Any],
    days: list[date], tz: tzinfo,
) -> dict[str, dict[str, dict[int, float]]]:
    """Phase 138: {date: {"ref": {hour: kWh}, "cloud": {hour: %}}} for each of ``days`` from one
    archive response (the same hourly layout as the live forecast). Days with no sunlight are left out."""
    out: dict[str, dict[str, dict[int, float]]] = {}
    for day in days:
        ref = om_reference_hours(times_utc, gti_by_string, kwp_by_string, day, tz)
        if any(v > 0 for v in ref.values()):
            out[day.isoformat()] = {"ref": ref, "cloud": om_cloud_hours(times_utc, cloud, day, tz)}
    return out


def stats_hourly_kwh(rows: list[dict[str, Any]], tz: tzinfo) -> dict[str, dict[int, float]]:
    """Phase 138: {date: {local hour: kWh}} from HA long-term statistics rows ({"start", "change"}).

    ``start`` may be a datetime, an ISO string or a UNIX timestamp (seconds or milliseconds).
    Missing or negative changes are left out, never read as 0.
    """
    out: dict[str, dict[int, float]] = {}
    for row in rows:
        start, change = row.get("start"), _num(row.get("change"))
        if change is None or change < 0:
            continue
        if isinstance(start, datetime):
            when = start
        elif isinstance(start, (int, float)):
            when = datetime.fromtimestamp(start / 1000 if start > 1e11 else start, tz=timezone.utc)
        else:
            try:
                when = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            except ValueError:
                continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        local = when.astimezone(tz)
        day = out.setdefault(local.date().isoformat(), {})
        day[local.hour] = round(day.get(local.hour, 0.0) + change, 3)
    return out


def om_archive_training_hours(
    days: dict[str, dict[str, dict[int, float]]], pv: dict[str, dict[int, float]], lat: float, lon: float, tz: tzinfo,
) -> list[dict[str, Any]]:
    """Phase 138: training hours ({date, hour, ref_kwh, pv_kwh, cloud_pct, bin}, as om_training_hours
    builds) from archive days joined with measured hourly solar."""
    out: list[dict[str, Any]] = []
    for d in sorted(days):
        measured = pv.get(d) or {}
        day = date.fromisoformat(d)
        for hour, ref in sorted(days[d]["ref"].items()):
            actual = measured.get(hour)
            if actual is None or ref < OM_MIN_REF_KWH:
                continue
            el, az = sun_position(lat, lon, _mid_hour(day, hour, tz))
            out.append({"date": d, "hour": hour, "ref_kwh": ref, "pv_kwh": actual,
                        "cloud_pct": days[d]["cloud"].get(hour), "bin": sun_bin(el, az)})
    return out


def om_split_backfill(
    hours: list[dict[str, Any]], live_dates: set[str], today: date, history_days: int = OM_HISTORY_DAYS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Phase 138: stored backfill hours as (inside the learning window, older for shading only).
    Dates the live log already covers, and dates from today on, are dropped (live data wins)."""
    floor, top = (today - timedelta(days=history_days)).isoformat(), today.isoformat()
    window: list[dict[str, Any]] = []
    older: list[dict[str, Any]] = []
    for h in hours:
        d = h.get("date")
        if not isinstance(d, str) or d in live_dates or d >= top:
            continue
        (window if d >= floor else older).append(h)
    return window, older


def om_backtest(
    days: dict[str, dict[str, dict[int, float]]], hours: list[dict[str, Any]], actuals: dict[str, float],
    live: dict[str, float], lat: float, lon: float, tz: tzinfo, history_days: int = OM_HISTORY_DAYS,
) -> dict[str, Any]:
    """Phase 138: walk-forward replay of the archive days. Each day is predicted only from the
    training hours of earlier days (window + older for shading, as live), so nothing leaks from
    the future, and scored against the day's actual solar. ``live`` is the logged live corrected
    forecast per date; the error sums cover only days with both forecasts."""
    ordered = sorted(hours, key=lambda h: h["date"])
    om_err = live_err = om_only_err = 0.0
    scored = paired = learned = 0
    for d in sorted(days):
        actual = actuals.get(d)
        if actual is None:
            continue
        floor = (date.fromisoformat(d) - timedelta(days=history_days)).isoformat()
        window = [h for h in ordered if floor <= h["date"] < d]
        older = [h for h in ordered if h["date"] < floor]
        model = om_learn(window, older)
        pred = om_predict(days[d]["ref"], days[d]["cloud"], model, lat, lon, date.fromisoformat(d), tz)
        if pred["kwh"] is None:
            continue
        scored += 1
        learned += pred["basis"] == "learned"
        om_only_err += abs(pred["kwh"] - actual)
        if live.get(d) is not None:
            paired += 1
            om_err += abs(pred["kwh"] - actual)
            live_err += abs(live[d] - actual)
    return {
        "days": scored, "learned_days": learned, "paired_days": paired,
        "om_mae": round(om_only_err / scored, 2) if scored else None,
        "om_err_sum": round(om_err, 2), "live_err_sum": round(live_err, 2),
        "om_paired_mae": round(om_err / paired, 2) if paired else None,
        "live_mae": round(live_err / paired, 2) if paired else None,
    }
