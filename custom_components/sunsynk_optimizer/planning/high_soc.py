"""Time spent at a high battery state of charge (phase 116). Read-only measurement, no decisions."""
from __future__ import annotations

from typing import Any

HIGH_SOC_THRESHOLD = 95.0
FULL_SOC_THRESHOLD = 100.0
# A step longer than this is a gap (restart, outage), not time at that SOC.
HIGH_SOC_MAX_STEP_MINUTES = 60


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def high_soc_hours(samples: list[dict[str, Any]], threshold: float = HIGH_SOC_THRESHOLD) -> dict[str, Any] | None:
    """Hours at or above ``threshold`` and at 100%, from timed {minute, soc} samples.

    Each step between consecutive samples is credited to the earlier sample's SOC (as hourly_profile does).
    A step with no usable SOC, or longer than HIGH_SOC_MAX_STEP_MINUTES, adds nothing to the covered hours.
    Returns None for fewer than two samples; a partly covered day reports its `sample_hours` so it reads
    as partial, never as 0 (rule 11).
    """
    ordered = sorted((s for s in samples or [] if _num(s.get("minute")) is not None), key=lambda s: s["minute"])
    if len(ordered) < 2:
        return None
    high = full = covered = 0.0
    for cur, nxt in zip(ordered, ordered[1:]):
        step = nxt["minute"] - cur["minute"]
        soc = _num(cur.get("soc"))
        if soc is None or step <= 0 or step > HIGH_SOC_MAX_STEP_MINUTES:
            continue
        covered += step
        if soc >= threshold:
            high += step
        if soc >= FULL_SOC_THRESHOLD:
            full += step
    if covered <= 0:
        return None
    return {
        "high_soc_hours": round(high / 60, 2),
        "full_soc_hours": round(full / 60, 2),
        "high_soc_sample_hours": round(covered / 60, 2),
    }


def high_soc_totals(records: list[dict[str, Any]], today: str, days: int = 30) -> dict[str, Any]:
    """30-day and year-to-date sums of logged `high_soc_hours` from day_kpis records, with the days counted.

    Days without the field (logged before this phase, or too few samples) are not counted, so a short
    history reads as few days, not as zero hours. `today` is an ISO date and is included.
    """
    from datetime import date, timedelta

    try:
        end = date.fromisoformat(today)
    except (TypeError, ValueError):
        return {"high_soc_30d_hours": None, "high_soc_30d_days": 0, "high_soc_ytd_hours": None, "high_soc_ytd_days": 0}
    start30 = end - timedelta(days=days - 1)
    by_date: dict[str, float] = {}
    for rec in records or []:
        value = _num(rec.get("high_soc_hours")) if rec.get("type") == "day_kpis" else None
        if value is None or not rec.get("date"):
            continue
        by_date[str(rec["date"])] = value  # one value per date; the last record wins
    in30 = [v for d, v in by_date.items() if start30.isoformat() <= d <= end.isoformat()]
    ytd = [v for d, v in by_date.items() if f"{end.year}-01-01" <= d <= end.isoformat()]
    return {
        "high_soc_30d_hours": round(sum(in30), 1) if in30 else None, "high_soc_30d_days": len(in30),
        "high_soc_ytd_hours": round(sum(ytd), 1) if ytd else None, "high_soc_ytd_days": len(ytd),
    }
