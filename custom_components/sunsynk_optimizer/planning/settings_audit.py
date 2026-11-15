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


# ---- Phase 112: daily efficiency flags -------------------------------------------------------------
# Each rule finds its setting by key-name words (the cloud key names are unverified), then compares the
# value with a recommended one. A rule that matches no key is reported as unmatched so the first live
# run shows which names to add; a missing or unreadable value is never flagged (rule 11 spirit).

EFFICIENCY_EXPECTED: dict[str, float] = {"max_shutdown_soc": 15.0, "max_low_batt_soc": 20.0, "max_trickle_w": 0.0}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag_on(value: Any) -> bool | None:
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes", "enabled"):
        return True
    if text in ("0", "false", "off", "no", "disabled"):
        return False
    return None


def _find_key(settings: dict[str, Any], words: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
    for name in sorted(str(k) for k in settings):
        lowered = name.lower()
        if all(w in lowered for w in words) and not any(x in lowered for x in exclude):
            return name
    return None


def settings_efficiency_check(
    settings: Any, expected: dict[str, float] | None = None
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """(flags, matched rule -> key, unmatched rules) for settings that cost energy.

    flags are {key, value, expected, why}, sorted by key. Not a dict -> ([], {}, []).
    """
    if not isinstance(settings, dict):
        return [], {}, []
    exp = {**EFFICIENCY_EXPECTED, **(expected or {})}
    rules: list[tuple[str, tuple[str, ...], tuple[str, ...], str, Any, str]] = [
        ("shutdown", ("shutdown",), (), "max", exp["max_shutdown_soc"], "Shut Down floor higher than the battery needs"),
        ("low_batt", ("low", "cap"), ("shutdown", "restart"), "max", exp["max_low_batt_soc"], "Low Batt floor higher than the battery needs"),
        ("trickle", ("trickle",), (), "max", exp["max_trickle_w"], "grid trickle feed above 0 imports needlessly"),
        ("priority_load", ("priority",), (), "on", True, "Priority Load off"),
        ("low_noise", ("noise",), (), "on", False, "Low Noise Mode throttles the inverter"),
        ("use_timer", ("usetimer",), (), "on", True, "Use Timer off, so the plan's slots are ignored"),
    ]
    flags: list[dict[str, Any]] = []
    matched: dict[str, str] = {}
    unmatched: list[str] = []
    for rule, words, exclude, kind, want, why in rules:
        key = _find_key(settings, words, exclude)
        if key is None:
            unmatched.append(rule)
            continue
        matched[rule] = key
        raw = settings[key]
        if kind == "max":
            value = _num(raw)
            if value is not None and value > want:
                flags.append({"key": key, "value": value, "expected": f"<= {want:g}", "why": why})
        else:
            value = _flag_on(raw)
            if value is not None and value != want:
                flags.append({"key": key, "value": raw, "expected": "on" if want else "off", "why": why})
    return sorted(flags, key=lambda f: f["key"]), matched, unmatched


def settings_efficiency_flags(settings: Any, expected: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Just the flags from `settings_efficiency_check`."""
    return settings_efficiency_check(settings, expected)[0]
