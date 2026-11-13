# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Sunsynk cloud API reliability counters and verdicts (phase 101)."""

from __future__ import annotations

from typing import Any

from .core import _num, median

API_FAIL_RATE_ALERT_PCT = 10.0  # a day counts as unreliable above this failure rate...
API_ALERT_MIN_CALLS = 5         # ...and only with this many calls (a quiet day proves nothing)
API_ALERT_DAYS = 3              # consecutive unreliable days before the Repairs issue
API_HISTORY_DAYS = 14
API_LATENCY_KEEP = 500          # per-day cap on stored latencies
FAILURE_KINDS = ("timeout", "401", "5xx", "other")


def classify_failure(exc: BaseException) -> str:
    """Failure kind for an API exception: timeout, 401, 5xx or other (duck-typed, no aiohttp import)."""
    if isinstance(exc, TimeoutError):
        return "timeout"
    status = getattr(exc, "status", None)
    if status == 401:
        return "401"
    if isinstance(status, int) and status >= 500:
        return "5xx"
    return "other"


class ApiStats:
    """Today's call counts, failures by kind, re-logins and latencies; `take` returns and resets them."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.calls = 0
        self.failures = {k: 0 for k in FAILURE_KINDS}
        self.relogins = 0
        self.push_retries = 0
        self._latencies: list[float] = []

    def record(self, kind: str | None, latency_s: float) -> None:
        """One finished call: kind None for success, else a FAILURE_KINDS value."""
        self.calls += 1
        if kind is not None:
            self.failures[kind if kind in self.failures else "other"] += 1
        if len(self._latencies) < API_LATENCY_KEEP:
            self._latencies.append(latency_s)

    def note_relogin(self) -> None:
        self.relogins += 1

    def note_push_retry(self) -> None:
        self.push_retries += 1

    def take(self, date: str) -> dict[str, Any]:
        """The day's summary record (calls, failures, success rate, latency), then reset."""
        failed = sum(self.failures.values())
        out = {
            "type": "api_health",
            "date": date,
            "calls": self.calls,
            "failures_total": failed,
            "failures": dict(self.failures),
            "relogins": self.relogins,
            "push_retries": self.push_retries,
            "success_rate_pct": round(100 * (self.calls - failed) / self.calls, 1) if self.calls else None,
            "median_latency_s": round(median(self._latencies), 2) if self._latencies else None,
            "worst_latency_s": round(max(self._latencies), 2) if self._latencies else None,
        }
        self._reset()
        return out


def append_api_day(history: list[dict[str, Any]] | None, summary: dict[str, Any]) -> list[dict[str, Any]]:
    """History with `summary` added (replacing a same-date entry), newest last, capped at API_HISTORY_DAYS."""
    kept = [d for d in (history or []) if d.get("date") != summary.get("date")]
    return sorted([*kept, summary], key=lambda d: str(d.get("date")))[-API_HISTORY_DAYS:]


def api_unreliable_streak(history: list[dict[str, Any]] | None) -> int:
    """Consecutive most-recent days whose failure rate exceeded the threshold (with enough calls)."""
    streak = 0
    for day in reversed(history or []):
        calls, failed = _num(day.get("calls")), _num(day.get("failures_total"))
        if calls is None or failed is None or calls < API_ALERT_MIN_CALLS or 100 * failed / calls <= API_FAIL_RATE_ALERT_PCT:
            break
        streak += 1
    return streak


def api_alert_due(history: list[dict[str, Any]] | None) -> bool:
    return api_unreliable_streak(history) >= API_ALERT_DAYS


def api_week(history: list[dict[str, Any]] | None, days: int = 7) -> dict[str, Any]:
    """Totals over the last `days` entries for the weekly digest; success rate None with no calls."""
    window = (history or [])[-days:]
    calls = sum(int(_num(d.get("calls")) or 0) for d in window)
    failed = sum(int(_num(d.get("failures_total")) or 0) for d in window)
    worst = [w for w in (_num(d.get("worst_latency_s")) for d in window) if w is not None]
    return {
        "days": len(window),
        "calls": calls,
        "failures_total": failed,
        "success_rate_pct": round(100 * (calls - failed) / calls, 1) if calls else None,
        "worst_latency_s": max(worst) if worst else None,
        "relogins": sum(int(_num(d.get("relogins")) or 0) for d in window),
        "push_retries": sum(int(_num(d.get("push_retries")) or 0) for d in window),
    }
