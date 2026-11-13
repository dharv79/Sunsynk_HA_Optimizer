# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Daily health line and deploy notice (phase 100): "is it working, and which version?"."""

from __future__ import annotations

from typing import Any

from .core import _num
from .kpis import AUDIT_DAILY_TYPES
from .overnight import OUTCOME_ALERT_PCT

HEALTH_SOC_STALE_CHECK_H = 2.0    # no SOC update for this long: Check
HEALTH_SOC_STALE_PROBLEM_H = 6.0  # ...and this long: Problem


def records_today(records: list[dict[str, Any]], date: str) -> tuple[int, int]:
    """(daily record types written for `date`, types expected) from the JSONL records."""
    seen = {r.get("type") for r in records if r.get("date") == date}
    return sum(1 for t in AUDIT_DAILY_TYPES if t in seen), len(AUDIT_DAILY_TYPES)


def daily_health(
    *,
    version: str | None,
    api_ok: bool | None,
    charge_shortfall_pct: float | None,
    records_written: int | None,
    records_expected: int | None,
    guarded_errors: dict[str, int] | None,
    api_failures: int | None,
    soc_age_hours: float | None,
    shadow_features: list[str] | None,
) -> dict[str, Any]:
    """One-line health verdict for the 22:00 bundle and `sensor.health`. Pure.

    status is "Problem" (last push failed, or no SOC update for 6 h), "Check" (any
    guarded error or API failure, a missed charge target, a missing daily record, or
    a SOC update older than 2 h), "OK" only when every core input is known and clean,
    else None. A missing input is never read as healthy.
    """
    errors = {k: int(v) for k, v in (guarded_errors or {}).items() if v}
    error_count = sum(errors.values())
    shortfall = _num(charge_shortfall_pct)
    soc_age = _num(soc_age_hours)
    problems: list[str] = []
    checks: list[str] = []
    if api_ok is False:
        problems.append("last push failed")
    if soc_age is not None and soc_age > HEALTH_SOC_STALE_PROBLEM_H:
        problems.append(f"no SOC update for {soc_age:.0f} h")
    elif soc_age is not None and soc_age > HEALTH_SOC_STALE_CHECK_H:
        checks.append(f"SOC {soc_age:.1f} h old")
    if error_count:
        checks.append(f"{error_count} scheduled error(s)")
    if api_failures:
        checks.append(f"{api_failures} API failure(s)")
    if shortfall is not None and shortfall > OUTCOME_ALERT_PCT:
        checks.append(f"charge {shortfall:g}% short")
    if records_written is not None and records_expected is not None and records_written < records_expected:
        checks.append(f"{records_written}/{records_expected} daily records")
    core_known = api_ok is not None and soc_age is not None and records_written is not None and records_expected is not None
    if problems:
        status: str | None = "Problem"
    elif checks:
        status = "Check"
    else:
        status = "OK" if core_known else None
    return {
        "type": "daily_health",
        "status": status,
        "reasons": problems + checks,
        "version": version,
        "plan_pushed": api_ok,
        "charge_shortfall_pct": shortfall,
        "records_written": records_written,
        "records_expected": records_expected,
        "guarded_errors": errors,
        "api_failures": api_failures,
        "soc_age_hours": None if soc_age is None else round(soc_age, 1),
        "shadow_features": sorted(shadow_features or []),
    }


def deploy_changes(previous_types: list[str] | None, current_types: list[str]) -> list[str] | None:
    """Record types present now but not at the last deploy; None when the last deploy's list is unknown."""
    if not previous_types:
        return None
    return sorted(set(current_types) - set(previous_types))
