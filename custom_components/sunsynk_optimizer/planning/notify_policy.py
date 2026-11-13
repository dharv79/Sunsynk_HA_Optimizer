# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Notification priorities, quiet hours and the morning brief (phase 108). Pure; sending stays in the optimizer."""

from __future__ import annotations

from typing import Any

from .core import _num
from .reports import hhmm_to_minutes

NOTIFY_LEVELS = ("report", "info", "action")  # low to high
DEFAULT_NOTIFY_MIN_LEVEL = "report"  # send everything: no behaviour change until configured
HELD_LIMIT = 40
HELD_TEXT_CHARS = 220

# Title fragments (lower case). A warning title (⚠️) is always action; the rest is information unless listed here.
_ACTION_FRAGMENTS = ("grid outage", "stopped short", "inverter event", "not running", "free event")
_REPORT_FRAGMENTS = (
    "weekly insight", "tariff comparison", "battery value", "scorecard", "bigger system", "loads you could move",
    "load vs the weather", "morning brief",
)


def notification_level(title: str) -> str:
    """"action" (failed push, shortfall, outage, fault), "report" (weekly/monthly) or "info", from the title."""
    lowered = str(title).lower()
    # ⚠️ warnings and 🧪 test-plan results (the user just asked for them) always send.
    if str(title).startswith(("⚠", "🧪")) or any(f in lowered for f in _ACTION_FRAGMENTS):
        return "action"
    if any(f in lowered for f in _REPORT_FRAGMENTS):
        return "report"
    return "info"


def in_quiet_hours(minute_of_day: int, start: Any, end: Any) -> bool:
    """True inside [start, end) given as HH:MM, wrapping midnight; False when either is blank or invalid."""
    s, e = hhmm_to_minutes(start), hhmm_to_minutes(end)
    if s is None or e is None or s == e:
        return False
    return s <= minute_of_day < e if s < e else (minute_of_day >= s or minute_of_day < e)


def release_time(brief_time: Any, quiet_end: Any) -> str | None:
    """When held notifications go out: the brief time, else the end of quiet hours; None when neither is set."""
    for value in (brief_time, quiet_end):
        if hhmm_to_minutes(value) is not None:
            return str(value)
    return None


def delivery(level: str, min_level: Any, minute_of_day: int, quiet_start: Any, quiet_end: Any) -> str:
    """"send" now, or "hold". Action always sends; the rest need the minimum level and no quiet hours."""
    if level == "action":
        return "send"
    floor = NOTIFY_LEVELS.index(min_level) if min_level in NOTIFY_LEVELS else 0
    if NOTIFY_LEVELS.index(level) < floor or in_quiet_hours(minute_of_day, quiet_start, quiet_end):
        return "hold"
    return "send"


def hold_item(held: list[dict[str, Any]] | None, title: str, message: str, when: str) -> list[dict[str, Any]]:
    """The held list with one more {when, title, message} (message clipped), keeping the newest HELD_LIMIT."""
    text = " ".join(str(message).split())
    if len(text) > HELD_TEXT_CHARS:
        text = text[: HELD_TEXT_CHARS - 1] + "…"
    return [*(held or []), {"when": when, "title": title, "message": text}][-HELD_LIMIT:]


def _short_title(title: str) -> str:
    return str(title).split("Sunsynk:", 1)[-1].strip()


def morning_brief(
    held: list[dict[str, Any]], plan: dict[str, Any] | None, morning: dict[str, Any] | None,
    spare: dict[str, Any] | None, today: str,
) -> tuple[str, str]:
    """(title, message): last night's plan and result, today's forecast and spare solar, then what was held.

    Every part is optional and left out when its data is missing or from another day.
    """
    parts: list[str] = []
    plan, morning, spare = plan or {}, morning or {}, spare or {}
    target, soc = _num(plan.get("target_soc")), _num(morning.get("morning_soc"))
    if plan.get("date") == today and target is not None:
        line = f"Tonight's plan was a charge to {target:g}%"
        if soc is not None and morning.get("date") == today:
            line += f"; the battery is at {soc:g}% this morning"
        parts.append(line + ".")
    elif soc is not None and morning.get("date") == today:
        parts.append(f"Battery {soc:g}% this morning.")
    forecast = _num(plan.get("solar_forecast_kwh")) if plan.get("date") == today else None
    if forecast is not None:
        parts.append(f"Solar forecast today {forecast:g} kWh.")
    total = _num(spare.get("total_kwh")) if spare.get("date") == today else None
    if total is not None and total > 0:
        parts.append(f"About {total:g} kWh of spare solar expected.")
    if held:
        lines = [f"{h.get('when', '')} {_short_title(h.get('title', ''))}: {h.get('message', '')}".strip() for h in held]
        parts.append("Held since you were last told:\n- " + "\n- ".join(lines))
    return "🔋 Sunsynk: morning brief", "\n".join(parts) if parts else "Nothing to report."
