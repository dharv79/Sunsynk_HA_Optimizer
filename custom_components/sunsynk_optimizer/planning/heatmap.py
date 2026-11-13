# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Import / export / load heat-map data (phase 106): days down, 48 half-hours across. Pure."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .core import _num
from .load import day_load_slots

HEATMAP_KINDS = ("import", "export", "load")
HEATMAP_DEFAULT_DAYS = 28
HEATMAP_MAX_DAYS = 60
HEATMAP_SCALE_QUANTILE = 0.95  # colours clip here so one huge slot does not wash out the rest


def _clean(slots: Any) -> list[float] | None:
    if not isinstance(slots, (list, tuple)) or len(slots) != 48:
        return None
    out = []
    for v in slots:
        n = _num(v)
        if n is None:
            return None
        out.append(round(max(0.0, n), 3))
    return out


def heatmap_data(
    kind: str,
    half_import: dict[str, list[float]],
    half_export: dict[str, list[float]],
    snapshot_days: dict[str, dict[str, dict[str, Any]]],
    today: date,
    days: int = HEATMAP_DEFAULT_DAYS,
    bands: list[tuple[int, int, float]] | None = None,
) -> dict[str, Any]:
    """{kind, days: [{date, kwh: 48 values | None}], max_kwh, bands} for the last ``days`` days, oldest first.

    ``load`` is derived per day from the meter snapshots shaped by the import (phase 71); a day
    that cannot be derived is None, as is any day with no complete record. ``max_kwh`` is the
    95th percentile of the slots shown (0 when there are none), the colour-scale top. ``bands``
    are the price band edges ([start_min, end_min, p]) for the card to mark.
    """
    if kind not in HEATMAP_KINDS:
        raise ValueError(f"kind must be one of {HEATMAP_KINDS}")
    count = max(1, min(int(days), HEATMAP_MAX_DAYS))
    rows: list[dict[str, Any]] = []
    shown: list[float] = []
    for back in range(count, 0, -1):
        day = (today - timedelta(days=back)).isoformat()
        if kind == "import":
            slots = _clean(half_import.get(day))
        elif kind == "export":
            slots = _clean(half_export.get(day))
        else:
            snaps = snapshot_days.get(day)
            slots = _clean(day_load_slots(snaps, _clean(half_import.get(day)))) if snaps else None
        rows.append({"date": day, "kwh": slots})
        if slots:
            shown.extend(slots)
    top = 0.0
    if shown:
        ordered = sorted(shown)
        top = ordered[min(len(ordered) - 1, int(len(ordered) * HEATMAP_SCALE_QUANTILE))]
    return {
        "kind": kind,
        "days": rows,
        "max_kwh": round(top, 3),
        "bands": [[int(s), int(e), float(p)] for s, e, p in (bands or [])],
    }
