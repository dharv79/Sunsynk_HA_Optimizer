# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Weather-normalised load (phase 105): is the house using more, or is it just colder? Pure; reads logged records only."""

from __future__ import annotations

from math import sqrt
from typing import Any

from .core import _num
from .load import HEATING_BASE_C

LOAD_FIT_MIN_DAYS = 30  # home days needed outside the period being judged
LOAD_MONTH_MIN_DAYS = 10  # days in the judged month
LOAD_FIT_HISTORY_DAYS = 90  # records read for the daily comparison
LOAD_REPORT_HISTORY_DAYS = 420  # records read for the monthly report (13 months)
LOAD_FLAG_PCT = 8.0  # a month this far from the fitted expectation is worth saying
LOAD_FLAG_SIGMA = 2.0  # ... and must also clear this many standard errors
LOAD_BASE_SHIFT_PCT = 15.0  # month-on-month change in the fitted base load
LOAD_MONTH_FIT_MIN_DAYS = 14


def weather_load_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Home days with both a daily load and an outside temperature: {date, load_kwh, temp_c}, oldest first."""
    load: dict[str, float] = {}
    temp: dict[str, float] = {}
    away: set[str] = set()
    for r in records:
        date = r.get("date")
        if not isinstance(date, str):
            continue
        kind = r.get("type")
        if kind == "day_actuals" and (_num(r.get("day_load_kwh")) or 0) > 0:
            load[date] = float(r["day_load_kwh"])
        elif kind == "day_kpis" and _num(r.get("outside_temp_c")) is not None:
            temp[date] = float(r["outside_temp_c"])
        elif kind == "import_plan" and r.get("away"):
            away.add(date)
    return [
        {"date": d, "load_kwh": load[d], "temp_c": temp[d]}
        for d in sorted(set(load) & set(temp)) if d not in away
    ]


def fit_load_temp(rows: list[dict[str, Any]], min_days: int = LOAD_FIT_MIN_DAYS) -> dict[str, Any] | None:
    """Least squares of daily load on heating degrees max(0, 15.5 − °C): base kWh, kWh per degree, residual SD.

    A negative slope is no temperature effect (base = mean load, slope 0). None below ``min_days`` rows.
    """
    if len(rows) < min_days:
        return None
    xs = [max(0.0, HEATING_BASE_C - r["temp_c"]) for r in rows]
    ys = [r["load_kwh"] for r in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var >= 1.0 else 0.0
    slope = max(0.0, slope)
    base = my - slope * mx
    resid = [y - (base + slope * x) for x, y in zip(xs, ys)]
    sd = sqrt(sum(e * e for e in resid) / max(1, n - 2))
    return {"base_kwh": round(base, 2), "slope_kwh_per_c": round(slope, 3), "sd_kwh": round(sd, 2), "days": n}


def expected_load(fit: dict[str, Any], temp_c: float) -> float:
    """Fitted daily kWh at ``temp_c``."""
    return fit["base_kwh"] + fit["slope_kwh_per_c"] * max(0.0, HEATING_BASE_C - temp_c)


def load_vs_expected(rows: list[dict[str, Any]], date: str, load_kwh: Any, temp_c: Any) -> dict[str, Any] | None:
    """Today's load against the fit of every other home day: expected kWh, difference and percent. None when unfitted."""
    load, temp = _num(load_kwh), _num(temp_c)
    if load is None or temp is None:
        return None
    fit = fit_load_temp([r for r in rows if r["date"] != date])
    if fit is None:
        return None
    expected = expected_load(fit, temp)
    if expected <= 0:
        return None
    return {
        "date": date,
        "load_kwh": round(load, 2),
        "expected_kwh": round(expected, 2),
        "diff_kwh": round(load - expected, 2),
        "pct": round((load - expected) / expected * 100, 1),
        "temp_c": round(temp, 1),
        "base_kwh": fit["base_kwh"],
        "slope_kwh_per_c": fit["slope_kwh_per_c"],
        "days": fit["days"],
    }


def month_vs_fit(rows: list[dict[str, Any]], month: str) -> dict[str, Any] | None:
    """One month ("YYYY-MM") against the fit of all other home days, at the temperatures it actually had.

    ``flag`` is "higher"/"lower" when the mean difference is at least 8% of expectation and 2 standard
    errors; None otherwise. None when the month has under 10 days or the rest gives no fit.
    """
    inside = [r for r in rows if r["date"].startswith(month)]
    if len(inside) < LOAD_MONTH_MIN_DAYS:
        return None
    fit = fit_load_temp([r for r in rows if not r["date"].startswith(month)])
    if fit is None:
        return None
    expected = [expected_load(fit, r["temp_c"]) for r in inside]
    diffs = [r["load_kwh"] - e for r, e in zip(inside, expected)]
    n = len(inside)
    mean_expected = sum(expected) / n
    mean_diff = sum(diffs) / n
    pct = mean_diff / mean_expected * 100 if mean_expected > 0 else 0.0
    sem = fit["sd_kwh"] / sqrt(n)
    flag = None
    if abs(pct) >= LOAD_FLAG_PCT and sem > 0 and abs(mean_diff) >= LOAD_FLAG_SIGMA * sem:
        flag = "higher" if mean_diff > 0 else "lower"
    own = fit_load_temp(inside, LOAD_MONTH_FIT_MIN_DAYS)
    return {
        "month": month,
        "days": n,
        "mean_load_kwh": round(sum(r["load_kwh"] for r in inside) / n, 2),
        "mean_expected_kwh": round(mean_expected, 2),
        "diff_kwh": round(mean_diff, 2),
        "pct": round(pct, 1),
        "flag": flag,
        "base_kwh": own["base_kwh"] if own else None,
    }


def prior_month(month: str, back: int = 1) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + mon - 1 - back
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def monthly_load_report(rows: list[dict[str, Any]], month: str) -> dict[str, Any] | None:
    """``month`` against the fit, last month and the same month last year (the last once 13 months exist)."""
    this = month_vs_fit(rows, month)
    if this is None:
        return None
    last, last_year = month_vs_fit(rows, prior_month(month)), month_vs_fit(rows, prior_month(month, 12))
    base_shift = None
    if last and this["base_kwh"] and last["base_kwh"]:
        base_shift = round((this["base_kwh"] - last["base_kwh"]) / last["base_kwh"] * 100, 1)
    return {
        "month": month,
        "this": this,
        "last": last,
        "last_year": last_year,
        "base_shift_pct": base_shift,
        "base_flag": base_shift is not None and abs(base_shift) >= LOAD_BASE_SHIFT_PCT,
    }


def load_report_text(report: dict[str, Any] | None) -> str | None:
    """Notification text; None when there is no report or nothing unusual to say."""
    if not report:
        return None
    this = report["this"]
    if not this["flag"] and not report["base_flag"]:
        return None
    parts = []
    if this["flag"]:
        parts.append(
            f"{report['month']} load averaged {this['mean_load_kwh']:g} kWh/day against {this['mean_expected_kwh']:g} "
            f"expected for its temperatures ({this['pct']:+.0f}%, {this['flag']} than usual)"
        )
    if report["base_flag"]:
        parts.append(f"the temperature-independent base load moved {report['base_shift_pct']:+.0f}% on last month")
    return "; ".join(parts).capitalize() + ". Worth checking for a changed habit or a failing appliance."
