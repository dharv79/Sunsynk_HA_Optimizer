# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Running ledger of what solar and battery saved (phase 118). Pure; storage and sending stay in the optimizer.

The ledger is {date: [no_system_gbp, actual_gbp, basis]} with basis "day" (priced at that day's own
rates, logged at 22:00) or "est" (back-dated from old meter snapshots at today's rates). A "day" entry
is never replaced by an "est" one.
"""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from typing import Any

from .core import _num
from .kpis import no_system_baseline

BASIS_DAY = "day"
BASIS_ESTIMATE = "est"


def ledger_add(
    ledger: dict[str, list[Any]] | None, date: str, no_system: Any, actual: Any, basis: str = BASIS_DAY
) -> dict[str, list[Any]]:
    """A copy of `ledger` with `date` added. Missing figures leave it unchanged; an estimate never replaces a day entry."""
    out = {k: list(v) for k, v in (ledger or {}).items()}
    n, a = _num(no_system), _num(actual)
    if n is None or a is None or not isinstance(date, str):
        return out
    if basis == BASIS_ESTIMATE and out.get(date, [None, None, None])[2] == BASIS_DAY:
        return out
    out[date] = [round(n, 2), round(a, 2), basis]
    return out


def ledger_backfill(
    ledger: dict[str, list[Any]] | None,
    snapshot_days: dict[str, dict[str, dict[str, Any]]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    skip_dates: set[str] | None = None,
    today: str | None = None,
) -> tuple[dict[str, list[Any]], dict[str, int]]:
    """Back-date the ledger from stored meter snapshots at the current rates.

    Dates already in the ledger, today, free-event days (`skip_dates`) and days with an incomplete
    snapshot set or a missing price are left out, never counted as 0. Returns (ledger, counts).
    """
    out = {k: list(v) for k, v in (ledger or {}).items()}
    counts = {"added": 0, "skipped_free": 0, "incomplete": 0}
    for date in sorted(snapshot_days):
        if date in out or date == today:
            continue
        if date in (skip_dates or set()):
            counts["skipped_free"] += 1
            continue
        res = no_system_baseline(snapshot_days[date], prices_pence, export_bands)
        if res["no_system_gbp"] is None or res["system_actual_gbp"] is None:
            counts["incomplete"] += 1
            continue
        out[date] = [res["no_system_gbp"], res["system_actual_gbp"], BASIS_ESTIMATE]
        counts["added"] += 1
    return out, counts


def ledger_totals(ledger: dict[str, list[Any]] | None) -> dict[str, Any]:
    """All-time totals, plus per month, from the ledger. `since` is None while it is empty."""
    rows = sorted((ledger or {}).items())
    by_month: dict[str, dict[str, float]] = {}
    for date, (n, a, _basis) in rows:
        m = by_month.setdefault(str(date)[:7], {"no_system_gbp": 0.0, "actual_gbp": 0.0, "days": 0})
        m["no_system_gbp"] += n
        m["actual_gbp"] += a
        m["days"] += 1
    months = {
        k: {
            "no_system_gbp": round(v["no_system_gbp"], 2), "actual_gbp": round(v["actual_gbp"], 2),
            "saving_gbp": round(v["no_system_gbp"] - v["actual_gbp"], 2), "days": v["days"],
        }
        for k, v in sorted(by_month.items())
    }
    without = round(sum(r[1][0] for r in rows), 2)
    actual = round(sum(r[1][1] for r in rows), 2)
    return {
        "days": len(rows),
        "estimated_days": sum(1 for _d, r in rows if r[2] == BASIS_ESTIMATE),
        "since": rows[0][0] if rows else None,
        "no_system_gbp": without,
        "actual_gbp": actual,
        "saving_gbp": round(without - actual, 2),
        "by_month": months,
    }


def backfill_text(totals: dict[str, Any], counts: dict[str, int]) -> str:
    """The one-off message after a back-date; empty when nothing was added."""
    if not counts.get("added"):
        return ""
    return (
        f"Back-dated {counts['added']} days from {totals['since']}. Without solar or battery they would have cost "
        f"£{totals['no_system_gbp']:.2f}; you paid £{totals['actual_gbp']:.2f}, a saving of £{totals['saving_gbp']:.2f}. "
        "Old days use today's tariff rates, so treat them as an estimate. New days use that day's own rates."
    )


PAYBACK_INVESTMENT_GBP = 9000.0
PAYBACK_START = "2025-06-01"
PAYBACK_WINDOW_DAYS = 90


def payback_summary(
    ledger: dict[str, list[Any]] | None,
    opening_gbp: Any = 0.0,
    today: str | None = None,
    investment_gbp: float = PAYBACK_INVESTMENT_GBP,
    window_days: int = PAYBACK_WINDOW_DAYS,
) -> dict[str, Any]:
    """Progress of the savings towards paying back the investment (phase 119).

    saved = the user's opening balance (savings before tracking began) + the ledger total. The rate is the
    saving per day over the last `window_days` of ledger days; with fewer than 7 such days, or a rate of 0 or
    less, the payback date is None (unknown, never a guess).
    """
    opening = float(opening_gbp) if isinstance(opening_gbp, (int, float)) and not isinstance(opening_gbp, bool) else 0.0
    rows = {d: r for d, r in (ledger or {}).items() if isinstance(r, (list, tuple)) and len(r) >= 2}
    tracked = round(sum(r[0] - r[1] for r in rows.values()), 2)
    saved = round(opening + tracked, 2)
    remaining = round(max(investment_gbp - saved, 0.0), 2)
    out: dict[str, Any] = {
        "investment_gbp": round(investment_gbp, 2), "opening_gbp": round(opening, 2), "tracked_gbp": tracked,
        "saved_gbp": saved, "remaining_gbp": remaining,
        "percent": round(min(max(saved / investment_gbp * 100.0, 0.0), 100.0), 1) if investment_gbp > 0 else None,
        "tracked_days": len(rows), "per_day_gbp": None, "payback_date": None, "years_left": None,
    }
    if not today:
        return out
    cutoff = (date_cls.fromisoformat(today) - timedelta(days=window_days)).isoformat()
    recent = [r for d, r in rows.items() if cutoff < d < today]
    if len(recent) < 7:
        return out
    per_day = sum(r[0] - r[1] for r in recent) / len(recent)
    out["per_day_gbp"] = round(per_day, 2)
    if remaining <= 0:
        out["years_left"] = 0.0
    elif per_day > 0:
        days_left = remaining / per_day
        out["years_left"] = round(days_left / 365.25, 1)
        out["payback_date"] = (date_cls.fromisoformat(today) + timedelta(days=int(days_left))).isoformat()
    return out
