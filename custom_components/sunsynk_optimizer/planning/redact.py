# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Diagnostics redaction, config time validation and entity-id/URL text helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote


_AUDIT_REDACT_PARTS = ("password", "username", "token", "secret", "key", "email", "plant_id", "serial", "account")


def redact_config(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of a config mapping with credentials and identifiers masked, for the diagnostics download."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        lowered = str(key).lower()
        if any(part in lowered for part in _AUDIT_REDACT_PARTS):
            out[key] = "**REDACTED**"
        else:
            out[key] = _redact_value(value)
    return out


def _redact_value(value: Any) -> Any:
    """Recurse into mappings and lists/tuples so a secret nested inside a list is still masked."""
    if isinstance(value, Mapping):
        return redact_config(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_REDACT_MIN_LEN = 4  # shorter values would blank ordinary numbers

_REDACTED = "**REDACTED**"


def valid_hhmm(value: Any) -> bool:
    """True for a 24-hour "HH:MM" string, the only time form the tariff and Flux maths accept."""
    return isinstance(value, str) and bool(_HHMM_RE.match(value))


def time_field_errors(fields: Mapping[str, Any], pairs: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    """Form errors for time fields (phase 93): `invalid_time` per bad field, `same_time` on an equal start/end pair."""
    errors = {name: "invalid_time" for name, value in fields.items() if not valid_hhmm(value)}
    for start, end in pairs:
        if start not in errors and end not in errors and fields.get(start) == fields.get(end):
            errors[end] = "same_time"
    return errors


def config_time_problems(charges: Any, flux_products: Any) -> list[str]:
    """Saved tariff/Flux times that are not HH:MM, as short labels for the Repairs issue."""
    problems: list[str] = []
    for label, rows, keys in (("tariff row", charges, ("startRange", "endRange")), ("Flux", flux_products, ("startTime", "endTime"))):
        for idx, row in enumerate(rows if isinstance(rows, list) else [], 1):
            for key in keys:
                value = row.get(key) if isinstance(row, Mapping) else None
                if not valid_hhmm(value):
                    problems.append(f"{label} {idx} {'start' if key.startswith('start') else 'end'} '{value}'")
    return problems


def sensitive_values(config: Mapping[str, Any]) -> list[str]:
    """Identifying values in a config, longest first, for redaction by value (phase 93).

    Covers every value under a masked key (plant id, serial, username, ...), the
    MPAN/meter-serial parts of Octopus entity ids, and the person and calendar entities.
    """
    found: set[str] = set()
    for key, value in config.items():
        lowered = str(key).lower()
        text = "" if value is None or isinstance(value, (bool, Mapping, list, tuple)) else str(value).strip()
        if not text:
            continue
        if any(part in lowered for part in _AUDIT_REDACT_PARTS):
            found.add(text)
        elif "person" in lowered or "calendar" in lowered:
            found.update(part.strip() for part in text.split(","))
        elif "octopus" in lowered or "octopus" in text.lower():
            found.update(part for part in re.split(r"[._]", text) if len(part) >= 6 and any(ch.isdigit() for ch in part))
    return sorted((v for v in found if len(v) >= _REDACT_MIN_LEN), key=len, reverse=True)


def redact_values(data: Any, values: list[str]) -> Any:
    """Copy of `data` with every occurrence of a sensitive value masked in every string, at any depth."""
    if not values:
        return data
    pattern = re.compile("|".join(re.escape(v) for v in values), re.IGNORECASE)

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return pattern.sub(_REDACTED, item)
        if isinstance(item, Mapping):
            return {key: walk(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(value) for value in item]
        return item

    return walk(data)


def redact_diagnostics(config: Mapping[str, Any], state: Mapping[str, Any], paired_days: list[Any]) -> dict[str, Any]:
    """The diagnostics download: masked by key, then by value everywhere (state, error URLs, paired days)."""
    return redact_values(
        {"config": redact_config(config), "state": redact_config(state), "paired_days": paired_days},
        sensitive_values(config),
    )


STATE_TEXT_LIMIT = 255  # Home Assistant's limit for an entity state


def clip_text(value: Any, limit: int = STATE_TEXT_LIMIT) -> Any:
    """Strings longer than `limit` cut to fit, ending in "…"; anything else unchanged (phase 92)."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def entity_slug(name: str) -> str:
    """Lower-case id part for an entity name: runs of other characters become one underscore."""
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def remap_entity_ids(text: str, mapping: Mapping[str, str]) -> str:
    """Replace whole entity ids in rendered dashboard text (phase 94); a longer id is never part-matched."""
    wanted = {old: new for old, new in mapping.items() if old and new and old != new}
    if not wanted:
        return text
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in sorted(wanted, key=len, reverse=True)) + r")(?![\w])")
    return pattern.sub(lambda m: wanted[m.group(1)], text)


def url_segment(value: Any) -> str:
    """A config id made safe for one URL path segment or query value ('/', '?', '&', '..' can't redirect the call)."""
    return quote(str(value).strip(), safe="")
