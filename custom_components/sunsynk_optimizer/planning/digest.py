# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Weekly digest additions (phase 104): why the week cost what it did. Pure; reads logged records only."""

from __future__ import annotations

from typing import Any

from .core import IMPORT_CAUSES, _num
from .kpis import KPI_SENSORS

DIGEST_MISS_PCT = 3.0  # a night this many points short of target counts as a miss (below the 5-point alert)

# Weekly twins of KPI_SENSORS: (sensor key, week_kpis field, name, unit). Plain measurements: a weekly total is not a resetting total.
WEEKLY_KPI_SENSORS = tuple(
    (key.replace("kpi_", "kpi_week_", 1), f"week_{field}", name.replace(" today", " last week"), unit)
    for key, field, name, unit, _kind in KPI_SENSORS
)


def _in_week(records: list[dict[str, Any]], rtype: str, start: str, end: str) -> list[dict[str, Any]]:
    """One record per date of `rtype` dated within [start, end] (the last logged wins)."""
    by_date = {
        str(r["date"]): r for r in records
        if r.get("type") == rtype and isinstance(r.get("date"), str) and start <= r["date"] <= end
    }
    return [by_date[d] for d in sorted(by_date)]


def weekly_import_causes(
    records: list[dict[str, Any]], paired_days: list[dict[str, Any]], start: str, end: str
) -> dict[str, Any] | None:
    """Grid import by cause over the week (phase 42 ledger): kWh, and £ at each day's own average price.

    A day's price is its settled import cost over its import kWh, so £ covers only days with both and
    is None when none has. None overall when the ledger has no record in the week.
    """
    rows = _in_week(records, "import_causes", start, end)
    if not rows:
        return None
    cost = {d.get("date"): d for d in paired_days}
    kwh = {c: 0.0 for c in IMPORT_CAUSES}
    gbp = {c: 0.0 for c in IMPORT_CAUSES}
    priced = 0
    for row in rows:
        day = cost.get(row["date"]) or {}
        spent, imported = _num(day.get("actual_import_cost_gbp")), _num(day.get("day_grid_import_kwh"))
        price = spent / imported if spent is not None and imported else None
        priced += price is not None
        for cause in IMPORT_CAUSES:
            value = _num(row.get(cause)) or 0.0
            kwh[cause] += value
            if price is not None:
                gbp[cause] += value * price
    grid = [v for v in (_num(r.get("grid_import_kwh")) for r in rows) if v is not None]
    return {
        "days": len(rows),
        "kwh": {c: round(v, 2) for c, v in kwh.items() if v},
        "total_kwh": round(sum(kwh.values()), 2),
        "grid_import_kwh": round(sum(grid), 2) if grid else None,
        "gbp": {c: round(v, 2) for c, v in gbp.items() if kwh[c]} if priced else None,
        "priced_days": priced,
    }


def weekly_charge_shortfalls(records: list[dict[str, Any]], start: str, end: str) -> dict[str, Any] | None:
    """Nights that missed the overnight charge target (phase 54 records); None when no night was logged."""
    rows = _in_week(records, "charge_outcome", start, end)
    if not rows:
        return None
    missed = [
        {
            "date": r["date"], "target_soc": r.get("target_soc"), "reached_soc": r.get("reached_soc"),
            "shortfall_pct": r["shortfall_pct"], "likely_cause": r.get("likely_cause"),
        }
        for r in rows
        if (_num(r.get("shortfall_pct")) or 0) > DIGEST_MISS_PCT
    ]
    gaps = [_num(r.get("shortfall_pct")) for r in rows if _num(r.get("shortfall_pct")) is not None]
    return {
        "nights": len(rows),
        "missed": missed,
        "worst_pct": max(gaps) if gaps else None,
        "causes": {c: sum(1 for m in missed if m["likely_cause"] == c) for c in sorted({m["likely_cause"] for m in missed if m["likely_cause"]})},
    }


def weekly_forecast_error(paired_days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Raw against corrected solar forecast over the week's paired days (kWh); None with no complete day.

    Bias is forecast minus actual (positive = over-forecast). The corrected figure is the one the plan used.
    """
    rows = []
    for d in paired_days:
        actual, raw, corrected = _num(d.get("actual_solar_kwh")), _num(d.get("raw_forecast_kwh")), _num(d.get("solar_forecast_kwh"))
        if None not in (actual, raw, corrected):
            rows.append((raw - actual, corrected - actual))
    if not rows:
        return None
    n = len(rows)
    return {
        "days": n,
        "raw_mae_kwh": round(sum(abs(r) for r, _ in rows) / n, 2),
        "corrected_mae_kwh": round(sum(abs(c) for _, c in rows) / n, 2),
        "raw_bias_kwh": round(sum(r for r, _ in rows) / n, 2),
        "corrected_bias_kwh": round(sum(c for _, c in rows) / n, 2),
    }


def scorecard_verdicts(card: dict[str, dict[str, Any]] | None) -> dict[str, str]:
    """{feature: verdict} from a shadow_scorecard result."""
    return {name: str(s.get("verdict")) for name, s in (card or {}).items() if isinstance(s, dict)}


def verdict_changes(previous: dict[str, str] | None, current: dict[str, str]) -> list[dict[str, str]]:
    """Features whose verdict changed since `previous`. Empty on the first run (no baseline to compare)."""
    if not previous:
        return []
    return [
        {"feature": f, "from": previous[f], "to": v}
        for f, v in sorted(current.items())
        if f in previous and previous[f] != v
    ]


def weekly_kpi_values(record: dict[str, Any] | None) -> dict[str, float | None]:
    """Last week's KPI roll-up → {weekly sensor key: value}. Missing or non-numeric is None, never 0."""
    rec = record if isinstance(record, dict) else {}
    return {key: _num(rec.get(field)) for key, field, _name, _unit in WEEKLY_KPI_SENSORS}


def digest_text(digest: dict[str, Any] | None) -> str:
    """One plain sentence per available section, for the AI insight prompt. Empty when nothing is known."""
    d = digest or {}
    parts: list[str] = []
    causes = d.get("import_causes")
    if causes and causes.get("kwh"):
        top = sorted(causes["kwh"].items(), key=lambda kv: -kv[1])[:3]
        parts.append("Grid import by cause (kWh): " + ", ".join(f"{c.replace('_', ' ')} {v:g}" for c, v in top) + ".")
    charge = d.get("charge")
    if charge:
        parts.append(f"{len(charge['missed'])} of {charge['nights']} nights missed the charge target.")
    fc = d.get("forecast")
    if fc:
        parts.append(f"Solar forecast error: raw {fc['raw_mae_kwh']:g} kWh a day, corrected {fc['corrected_mae_kwh']:g} kWh.")
    trip = d.get("round_trip") or {}
    if trip.get("battery_eff") is not None:
        cold = (trip.get("cold") or {}).get("battery_eff")
        parts.append(
            f"Measured battery round trip {trip['battery_eff'] * 100:.1f}% (about {trip['ac_eff'] * 100:.0f}% at the AC side)"
            + (f", {cold * 100:.1f}% on cold days" if cold is not None else "") + "."
        )
    for change in d.get("shadow_changes") or []:
        parts.append(f"Shadow feature {change['feature']} went {change['from']} to {change['to']}.")
    return " ".join(parts)
