# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Inverter settings snapshot and change audit (phase 107). Pure; the read and the notification live in opt_monitor."""

from __future__ import annotations

from typing import Any

# Keys that change on every read or that the integration writes itself, so are never a finding.
AUDIT_IGNORE_KEYS = frozenset({"sdBatteryCurrent", "sn", "id"})
AUDIT_IGNORE_SUFFIXES = ("updatetime", "update_time", "createtime", "create_time", "updatedat", "createdat", "timestamp")
AUDIT_MAX_LISTED = 12  # changes quoted in the notification; the log holds them all


def snapshot_settings(settings: Any, ignore: frozenset[str] = AUDIT_IGNORE_KEYS) -> dict[str, str]:
    """Scalar settings as {key: text}, minus the keys that are never a finding. Non-dict input gives {}."""
    if not isinstance(settings, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in settings.items():
        name = str(key)
        if name in ignore or name.lower().endswith(AUDIT_IGNORE_SUFFIXES):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = "" if value is None else str(value)
    return out


def _same(old: str, new: str) -> bool:
    if old == new:
        return True
    try:
        return float(old) == float(new)
    except ValueError:
        return False


def settings_changes(previous: dict[str, str] | None, current: dict[str, str]) -> list[dict[str, str | None]]:
    """{key, old, new} for each key that differs, appeared or vanished (old/new None); [] with no previous snapshot.

    Sorted by key. A numeric value that only changed its text ("5" -> "5.0") is not a change.
    """
    if not previous or not current:
        return []
    out: list[dict[str, str | None]] = []
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key), current.get(key)
        if old is None or new is None:
            out.append({"key": key, "old": old, "new": new})
        elif not _same(old, new):
            out.append({"key": key, "old": old, "new": new})
    return out


def settings_change_text(changes: list[dict[str, str | None]], limit: int = AUDIT_MAX_LISTED) -> str | None:
    """"key: old → new" lines joined for one notification; None when there are no changes."""
    if not changes:
        return None
    shown = [
        f"{c['key']}: {'(none)' if c['old'] is None else c['old'] or '(blank)'} → "
        f"{'(none)' if c['new'] is None else c['new'] or '(blank)'}"
        for c in changes[:limit]
    ]
    more = f" (+{len(changes) - limit} more)" if len(changes) > limit else ""
    return "; ".join(shown) + more
