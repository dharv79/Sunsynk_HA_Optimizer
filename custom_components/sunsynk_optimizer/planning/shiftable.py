# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Shiftable-load report (phase 102): recurring daytime import blocks and what moving them overnight is worth.

Pure. Works on the logged half-hourly import (48 kWh slots per day); no appliance sensors.
"""

from __future__ import annotations

from datetime import date
from statistics import median
from typing import Any

from .core import _num
from .reports import band_price

SHIFT_WINDOW_DAYS = 28
SHIFT_MIN_DAYS = 7  # days of history before any report
SHIFT_MIN_EXCESS_KWH = 0.3  # a slot this far above its usual import is part of a block
SHIFT_MIN_BLOCK_KWH = 0.8  # smaller blocks are noise
SHIFT_MAX_SLOTS = 6  # longer runs are a general high-use stretch, not an appliance
SHIFT_RECUR_DAYS = 4  # a block must appear on this many days
SHIFT_START_TOLERANCE = 2  # start slots within this many half-hours are the same block
SHIFT_MIN_SAVING_GBP = 0.5  # monthly, below which a block is not worth listing
SHIFT_MAX_BLOCKS = 5
CHEAP_WINDOW = (120, 300)  # 02:00-05:00, minutes


def _valid_day(slots: Any) -> bool:
    return (
        isinstance(slots, (list, tuple))
        and len(slots) == 48
        and all(_num(v) is not None and v >= 0 for v in slots)
    )


def _blocks_for_day(slots: list[float], baseline: list[float], eligible: list[bool]) -> list[dict[str, Any]]:
    """Runs of consecutive eligible slots standing above baseline, as {start, slots, kwh}."""
    out: list[dict[str, Any]] = []
    run: list[int] = []

    def close() -> None:
        if run:
            kwh = sum(slots[i] - baseline[i] for i in run)
            if kwh >= SHIFT_MIN_BLOCK_KWH and len(run) <= SHIFT_MAX_SLOTS:
                out.append({"start": run[0], "slots": list(run), "kwh": kwh,
                            "excess": {i: slots[i] - baseline[i] for i in run}})
            run.clear()

    for i in range(48):
        if eligible[i] and slots[i] - baseline[i] >= SHIFT_MIN_EXCESS_KWH:
            run.append(i)
        else:
            close()
    close()
    return out


def _hhmm(slot: float) -> str:
    minutes = int(round(slot)) * 30
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def shiftable_loads(
    half_hourly_import: dict[str, list[float]],
    day_bands: list[tuple[int, int, float]],
    cheap_p: float | None,
    today: date,
    headroom_kw: float | None = None,
    skip_dates: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Recurring import blocks outside the cheap window, with the monthly £ if moved into it.

    A block is a run of 1-6 half-hours whose import is well above that slot's usual (lower-quartile) import over the
    window, in slots priced above ``cheap_p``. Blocks on different days merge when their start
    slots are within two half-hours; one is reported when it recurs on 4+ days. The saving is
    the excess kWh at the slot prices minus the same kWh at ``cheap_p``, per occurrence, scaled
    to 30 days. ``fits`` is whether the block's average kW fits in ``headroom_kw`` (None when
    unknown). None below 7 usable days or without a cheap price.
    """
    if cheap_p is None:
        return None
    days = {}
    for day_str, slots in half_hourly_import.items():
        if day_str in skip_dates or not _valid_day(slots):
            continue
        try:
            d = date.fromisoformat(day_str)
        except (TypeError, ValueError):
            continue
        if 0 < (today - d).days <= SHIFT_WINDOW_DAYS:
            days[day_str] = [float(v) for v in slots]
    if len(days) < SHIFT_MIN_DAYS:
        return None
    # Lower quartile, not median: a block recurring on half the days would otherwise lift its own baseline.
    baseline = [sorted(day[i] for day in days.values())[len(days) // 4] for i in range(48)]
    prices = [band_price(day_bands, i * 30) for i in range(48)]
    eligible = [
        p is not None and p > cheap_p and not (CHEAP_WINDOW[0] <= i * 30 < CHEAP_WINDOW[1])
        for i, p in enumerate(prices)
    ]
    found = []
    for day_str, slots in days.items():
        for block in _blocks_for_day(slots, baseline, eligible):
            found.append((day_str, block))
    found.sort(key=lambda fb: fb[1]["start"])
    clusters: list[list[tuple[str, dict[str, Any]]]] = []
    for item in found:
        if clusters and item[1]["start"] - clusters[-1][-1][1]["start"] <= SHIFT_START_TOLERANCE:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    n_days = len(days)
    blocks = []
    for cluster in clusters:
        on_days = {day for day, _ in cluster}
        if len(on_days) < SHIFT_RECUR_DAYS:
            continue
        count = len(cluster)
        kwh = sum(b["kwh"] for _, b in cluster) / count
        start = median(b["start"] for _, b in cluster)
        length = round(sum(len(b["slots"]) for _, b in cluster) / count)
        extra_p = sum(
            sum(ex * (prices[i] - cheap_p) for i, ex in b["excess"].items()) for _, b in cluster
        ) / count
        per_month = len(on_days) / n_days * 30
        saving = extra_p / 100 * per_month
        if saving < SHIFT_MIN_SAVING_GBP:
            continue
        avg_kw = kwh / (max(1, length) * 0.5)
        blocks.append({
            "start": _hhmm(start),
            "length_h": round(length * 0.5, 1),
            "kwh": round(kwh, 2),
            "days": len(on_days),
            "per_month": round(per_month, 1),
            "avg_kw": round(avg_kw, 2),
            "fits": None if headroom_kw is None else avg_kw <= headroom_kw,
            "saving_gbp_month": round(saving, 2),
        })
    blocks.sort(key=lambda b: -b["saving_gbp_month"])
    blocks = blocks[:SHIFT_MAX_BLOCKS]
    return {
        "days": n_days,
        "blocks": blocks,
        "saving_gbp_month": round(sum(b["saving_gbp_month"] for b in blocks if b["fits"] is not False), 2),
    }


def shiftable_text(result: dict[str, Any] | None) -> str | None:
    """One-paragraph report line for the monthly notification; None when nothing to say."""
    if not result or not result.get("blocks"):
        return None
    parts = [
        f"~{b['start']} ({b['kwh']:g} kWh, {b['days']} days, £{b['saving_gbp_month']:.2f}/month"
        + (", may not fit under your supply limit" if b["fits"] is False else "") + ")"
        for b in result["blocks"]
    ]
    return (
        f"Regular daytime loads that could move to the cheap window (from {result['days']} days of import): "
        + "; ".join(parts) + f". Worth about £{result['saving_gbp_month']:.2f}/month if moved. Report only."
    )
