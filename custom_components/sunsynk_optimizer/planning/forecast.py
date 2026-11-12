# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Solar forecast correction, newsvendor, quantiles, weather bins, intraday scale, clipping and string checks."""

from __future__ import annotations

from datetime import date
from typing import Any

from .core import (
    CHARGE_EFFICIENCY,
    _is_number,
    _median,
    percentile,
)
from .overnight import (
    bridge_soc,
    walk_bridge_gap,
)


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


# Phase 29: clipped solar (battery full, export pinned at the inverter limit).
CLIPPED_SOC_PCT = 99.0

CLIPPED_EXPORT_FRACTION = 0.95


def is_clipping(soc: float | None, grid_pac_w: float | None, export_limit_w: float | None) -> bool:
    """True when the battery is full and export sits at ≥95% of the limit (grid_pac < 0 is export).

    Any missing input or a non-positive limit (feature off) is False, never a guess.
    """
    if soc is None or grid_pac_w is None or not export_limit_w or export_limit_w <= 0:
        return False
    return soc >= CLIPPED_SOC_PCT and -grid_pac_w >= CLIPPED_EXPORT_FRACTION * export_limit_w


VOLTAGE_DERATE_V = 253.0  # UK statutory upper limit is 253 V; inverters derate or trip export near it

DERATE_PV_MIN_W = 500.0

DERATE_USED_FRACTION = 0.8  # export + house load below this share of PV means solar is being curtailed


def voltage_derate_suspect(
    voltage_v: float | None,
    grid_pac_w: float | None,
    pv_w: float | None,
    load_w: float | None,
    soc: float | None,
) -> bool:
    """True when high grid voltage with a full battery looks to be curtailing solar (phase 83).

    Voltage at/above VOLTAGE_DERATE_V, battery full, PV at least DERATE_PV_MIN_W, and export plus
    house load under DERATE_USED_FRACTION of PV. grid_pac < 0 is export. Any missing input is
    False, never a guess (rule 11). Distinct from ``is_clipping`` (export pinned at the limit).
    """
    if None in (voltage_v, grid_pac_w, pv_w, load_w, soc):
        return False
    if voltage_v < VOLTAGE_DERATE_V or soc < CLIPPED_SOC_PCT or pv_w < DERATE_PV_MIN_W:
        return False
    return max(0.0, -grid_pac_w) + load_w < DERATE_USED_FRACTION * pv_w


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


STRING_MIN_DAY_KWH = 1.0       # sampled kWh needed for a day to count

STRING_MIN_HISTORY = 7         # prior days needed before judging

STRING_MIN_DELTA = 0.08        # share must move at least this much (8 points) to count

STRING_MAD_K = 3.0


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


# Self-learned probabilistic solar forecast (phase 70). Shadow: never replaces min(raw, corrected).
FORECAST_QUANTILE_LEVELS = (0.1, 0.5, 0.9)

FORECAST_QUANTILE_MIN_DAYS = 14

FORECAST_QUANTILE_RATIO_CAP = (0.05, 5.0)

CLOUD_BUCKET_EDGES = (35.0, 70.0)  # % cloud: below the first is clear, at/above the second overcast (phase 82)

CLOUD_BIN_MIN_DAYS = 5  # a cloud cell this thin falls back to the band x season bins


def cloud_bucket(cloud_pct: Any) -> str | None:
    """clear / mixed / overcast from a mean cloud-cover percentage; None when missing (never guessed)."""
    if not _is_number(cloud_pct):
        return None
    if cloud_pct < CLOUD_BUCKET_EDGES[0]:
        return "clear"
    return "mixed" if cloud_pct < CLOUD_BUCKET_EDGES[1] else "overcast"


def _season(month: int) -> set[int]:
    """The month and its two neighbours (Dec wraps to Jan)."""
    return {(month - 2) % 12 + 1, month, month % 12 + 1}


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile (0-1) of ``values``."""
    return percentile(values, q * 100.0)


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
        out.append({"date": d["date"], "ratio": ratio, "band": d.get("forecast_band"), "month": month,
                    "cloud": cloud_bucket(d.get("cloud_cover_pct"))})
    return out


def forecast_quantiles(
    samples: list[dict[str, Any]],
    month: int,
    band: str | None,
    raw_forecast_kwh: float | None,
    min_days: int = FORECAST_QUANTILE_MIN_DAYS,
    cloud: str | None = None,
) -> dict[str, Any] | None:
    """P10/P50/P90 of actual/raw from the narrowest bin with enough days.

    Bins, narrowest first: [cloud bucket × season, cloud bucket (phase 82, only
    when ``cloud`` is given, needing CLOUD_BIN_MIN_DAYS)], forecast band ×
    season (month ± 1), season, band, all. kWh = raw × ratio when a raw
    forecast is given. None below ``min_days`` in every bin (thin history is
    not a guess). Without ``cloud`` the result is the phase 70 one.
    """
    season = _season(month)
    bins = (
        ("cloud_season", lambda s: s.get("cloud") == cloud and s["month"] in season),
        ("cloud", lambda s: s.get("cloud") == cloud),
        ("band_season", lambda s: s["band"] == band and s["month"] in season),
        ("season", lambda s: s["month"] in season),
        ("band", lambda s: s["band"] == band),
        ("all", lambda s: True),
    )
    for name, keep in bins:
        if band is None and name.startswith("band"):
            continue
        if cloud is None and name.startswith("cloud"):
            continue
        ratios = sorted(s["ratio"] for s in samples if keep(s))
        if len(ratios) < (CLOUD_BIN_MIN_DAYS if name.startswith("cloud") else min_days):
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
