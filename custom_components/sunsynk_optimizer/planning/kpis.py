# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Meter snapshots, day/week KPIs, import causes, shadow scorecard, data audit."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    KPI_RESERVE_SOC,
    _BASELINE_SEGMENTS,
    _num,
    _pence,
    _seg_value,
    _snap,
    _snap_delta,
    hhmm_to_minutes,
    price_at,
    round_or_none,
)


KPI_LATE_LOAD_HOURS = 4.0  # 22:00 → next 02:00 charge

KPI_VERSION = 2  # 2 = phase 52 self-sufficiency (excludes battery grid-charge and free import)

DAY_KPI_FIELDS = (
    "grid_import_offpeak_kwh",
    "grid_import_day_kwh",
    "grid_import_morning_kwh",
    "grid_import_peak_kwh",
    "grid_import_evening_kwh",
    "self_sufficiency_pct",
    "grid_independence_pct",  # phase 52: the pre-v2 ratio (all import, incl. battery charging)
    "avoidable_import_gbp",
    "export_peak_kwh",
    "export_peak_gbp",
    "unused_charge_kwh",
    "battery_saving_gbp",  # phase 37
)


def _mul(*values: float | None) -> float | None:
    if any(v is None for v in values):
        return None
    return math.prod(values)


def _credit(free_credit: dict[str, tuple[float, float]] | None, segment: str, index: int = 0) -> float:
    return (free_credit or {}).get(segment, (0.0, 0.0))[index]


def _less(value: float | None, credit: float) -> float | None:
    return None if value is None else max(0.0, value - credit)


# Import while the battery still had charge and was discharging is load above
# the inverter's output (or a short spike), not a planning shortfall.
OVER_LIMIT_MARGIN_SOC = 5  # above KPI_RESERVE_SOC, so a battery near its floor still counts

OVER_LIMIT_MAX_GAP_MIN = 35  # samples further apart (restart) prove nothing


def kpi_segment(minute: int) -> str | None:
    """The `_BASELINE_SEGMENTS` start ("HH:MM") a minute of the day falls in; None from 22:00."""
    for start, end, _band, _ in _BASELINE_SEGMENTS:
        if hhmm_to_minutes(start) <= minute < hhmm_to_minutes(end):
            return start
    return None


def over_limit_import_step(prev: dict[str, Any] | None, sample: dict[str, Any]) -> tuple[str | None, float]:
    """Over-limit import between two samples {minute, soc, grid_import_kwh}: (segment, kWh).

    Samples are taken on every SOC change, so if both ends sit above the
    reserve + margin the battery never reached its floor in between. Import
    while the SOC fell (the battery was already discharging into the house)
    is load it could not cover. A flat SOC proves nothing — a slot target can
    hold the battery while the house runs on the grid — and a rising one is a
    grid charge. Anything unknown, a long gap or a meter reset → 0.
    """
    if prev is None:
        return None, 0.0
    values = (prev.get("minute"), prev.get("soc"), prev.get("grid_import_kwh"),
              sample.get("minute"), sample.get("soc"), sample.get("grid_import_kwh"))
    if any(v is None for v in values):
        return None, 0.0
    m0, soc0, imp0, m1, soc1, imp1 = values
    floor = KPI_RESERVE_SOC + OVER_LIMIT_MARGIN_SOC
    if not 0 < m1 - m0 <= OVER_LIMIT_MAX_GAP_MIN or imp1 < imp0:
        return None, 0.0
    if min(soc0, soc1) <= floor or soc1 >= soc0:
        return None, 0.0
    segment = kpi_segment(m0)
    return segment, (imp1 - imp0) if segment else 0.0


IMPORT_CAUSE_CHARGE_WINDOW = (2 * 60, 5 * 60)


def import_cause_step(prev: dict[str, Any] | None, sample: dict[str, Any]) -> tuple[str | None, float]:
    """Assign the grid import between two samples to one cause (phase 42): (cause, kWh).

    Samples {minute, soc, grid_import_kwh, slots_held, slot_hold}. A long gap, a meter reset
    or any unknown reading is not attributed (None, 0.0) - never guessed. Order of evidence:
    an event holding the slots; a rising SOC (grid charge); a falling SOC above the floor
    (load above what the inverter can discharge, as over_limit_import_step); SOC at the floor;
    a flat SOC while a slot target is known to hold the battery; otherwise 'unknown'.
    """
    if prev is None:
        return None, 0.0
    values = (prev.get("minute"), prev.get("soc"), prev.get("grid_import_kwh"),
              sample.get("minute"), sample.get("soc"), sample.get("grid_import_kwh"))
    if any(v is None for v in values):
        return None, 0.0
    m0, soc0, imp0, m1, soc1, imp1 = values
    if not 0 < m1 - m0 <= OVER_LIMIT_MAX_GAP_MIN or imp1 < imp0:
        return None, 0.0
    kwh = imp1 - imp0
    if kwh <= 0:
        return None, 0.0
    if sample.get("slots_held") or prev.get("slots_held"):
        return "free_event", kwh
    if soc1 > soc0:
        return "grid_charge", kwh
    floor = KPI_RESERVE_SOC + OVER_LIMIT_MARGIN_SOC
    if soc1 < soc0 and min(soc0, soc1) > floor:
        return "load_over_limit", kwh
    if min(soc0, soc1) <= floor:
        return "battery_floor", kwh
    if sample.get("slot_hold") or IMPORT_CAUSE_CHARGE_WINDOW[0] <= m0 < IMPORT_CAUSE_CHARGE_WINDOW[1]:
        return "slot_hold", kwh
    return "unknown", kwh


def day_kpis(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    battery_capacity_kwh: float,
    late_load_kw: float | None,
    free_credit: dict[str, tuple[float, float]] | None = None,
    over_limit: dict[str, float] | None = None,
) -> dict[str, float | None]:
    """Efficiency KPIs for 00:00-22:00 from the day's meter snapshots.

    `snapshots` maps "HH:MM" → {grid_import_kwh, grid_export_kwh, load_kwh, soc}.
    `prices_pence` has "offpeak", "day", "peak" (import) and "export_peak".
    Any KPI whose inputs are missing is None, never 0 (do-not-break 11).

    - Import bands: off-peak 02:00-05:00, peak 16:00-19:00, day = the rest
      (00:00-02:00, 05:00-16:00, 19:00-22:00). Morning = 05:00-16:00, the
      part of the day band an overnight shortfall shows up in (phase 15).
    - Avoidable import £ = day + peak import at their prices (what a perfect
      plan would have shifted to off-peak).
    - Unused charge = overnight SOC added that was still spare at 16:00 above
      the evening need (reserve + 16:00-22:00 load in hindsight + 22:00-02:00
      at `late_load_kw`), capped at what the night added.
    - `free_credit` (free_event_kpi_credit): free-event import per segment,
      left out of the import bands and avoidable £ (it cost nothing).
    - `over_limit` (segment → kWh, over_limit_import_step): import while the
      battery still had charge; stays in the bands (it was paid for) but is
      left out of avoidable £ (no plan could have shifted it).
    """
    imp = "grid_import_kwh"
    offpeak = _less(_snap_delta(snapshots, "02:00", "05:00", imp), _credit(free_credit, "02:00"))
    peak = _less(_snap_delta(snapshots, "16:00", "19:00", imp), _credit(free_credit, "16:00"))
    morning = _less(_snap_delta(snapshots, "05:00", "16:00", imp), _credit(free_credit, "05:00"))
    evening = _less(_snap_delta(snapshots, "19:00", "22:00", imp), _credit(free_credit, "19:00"))
    parts = (_less(_snap(snapshots, "02:00", imp), _credit(free_credit, "00:00")), morning, evening)
    day = None if any(p is None for p in parts) else sum(parts)

    load22, imp22 = _snap(snapshots, "22:00", "load_kwh"), _snap(snapshots, "22:00", imp)
    self_sufficiency = grid_independence = None
    soc02_ss, soc05_ss = _snap(snapshots, "02:00", "soc"), _snap(snapshots, "05:00", "soc")
    if load22 is not None and imp22 is not None and load22 > 0:
        grid_independence = max(0.0, min(100.0, 100.0 * (1 - imp22 / load22)))
        if soc02_ss is not None and soc05_ss is not None and battery_capacity_kwh > 0:
            # Phase 52: paid import that served the house = total less free import and
            # the 02:00-05:00 battery gain (grid-charged, not consumed).
            free_kwh = sum(c[0] for c in (free_credit or {}).values())
            gain_kwh = max(0.0, soc05_ss - soc02_ss) / 100 * battery_capacity_kwh
            house_import = max(0.0, imp22 - free_kwh - gain_kwh)
            self_sufficiency = max(0.0, min(100.0, 100.0 * (1 - house_import / load22)))

    avoidable_pence = over_limit_kwh = None
    over = over_limit or {}
    if day is not None and peak is not None:
        avoid_day = sum(_less(p, over.get(seg, 0.0)) for p, seg in zip(parts, ("00:00", "05:00", "19:00")))
        avoid_peak = _less(peak, over.get("16:00", 0.0))
        over_limit_kwh = day + peak - avoid_day - avoid_peak
        avoidable_pence = _mul(avoid_day, prices_pence.get("day"))
        peak_pence = _mul(avoid_peak, prices_pence.get("peak"))
        avoidable_pence = None if avoidable_pence is None or peak_pence is None else avoidable_pence + peak_pence

    export_peak = _snap_delta(snapshots, "16:00", "19:00", "grid_export_kwh")
    export_peak_pence = _mul(export_peak, prices_pence.get("export_peak"))

    unused = None
    soc02, soc05, soc16 = (_snap(snapshots, t, "soc") for t in ("02:00", "05:00", "16:00"))
    evening_load = _snap_delta(snapshots, "16:00", "22:00", "load_kwh")
    if None not in (soc02, soc05, soc16, evening_load, late_load_kw) and battery_capacity_kwh > 0:
        added_kwh = max(0.0, soc05 - soc02) / 100 * battery_capacity_kwh
        need_soc = KPI_RESERVE_SOC + (evening_load + late_load_kw * KPI_LATE_LOAD_HOURS) / battery_capacity_kwh * 100
        spare_kwh = max(0.0, soc16 - need_soc) / 100 * battery_capacity_kwh
        unused = min(added_kwh, spare_kwh)

    return {
        "grid_import_offpeak_kwh": round_or_none(offpeak),
        "grid_import_day_kwh": round_or_none(day),
        "grid_import_morning_kwh": round_or_none(morning),
        "grid_import_peak_kwh": round_or_none(peak),
        "grid_import_evening_kwh": round_or_none(evening),
        "self_sufficiency_pct": round_or_none(self_sufficiency, 1),
        "grid_independence_pct": round_or_none(grid_independence, 1),
        "kpi_version": KPI_VERSION,
        "avoidable_import_gbp": round_or_none(None if avoidable_pence is None else avoidable_pence / 100),
        "export_peak_kwh": round_or_none(export_peak),
        "export_peak_gbp": round_or_none(None if export_peak_pence is None else export_peak_pence / 100),
        "unused_charge_kwh": round_or_none(unused),
        "free_import_excluded_kwh": round(sum(c[0] for c in (free_credit or {}).values()), 2),
        "over_limit_import_kwh": round_or_none(over_limit_kwh),
    }


SOC_CAP_SHADOW_SOC = 85


def soc_cap_shadow(
    plan: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    battery_capacity_kwh: float,
    late_load_kw: float | None,
    prices_pence: dict[str, float | None],
    cap_soc: int = SOC_CAP_SHADOW_SOC,
) -> dict[str, Any] | None:
    """Shadow only: what capping tonight's low-solar target at `cap_soc` would have cost.

    None unless the 01:55 plan was a low-solar override above the cap (the
    weekly full-charge day is left out). The top `cut_soc` (charge reached
    above the cap) was needed only if the day's lowest SOC — snapshots from
    05:00 plus 22:00 projected to the 02:00 charge at `late_load_kw` — came
    within `cut_soc` of the discharge floor; that shortfall would have been
    bought at the day rate instead of off-peak. Missing inputs → None fields.
    """
    target = plan.get("target_soc")
    if not str(plan.get("target_soc_reason") or "").startswith("low_solar_override"):
        return None
    if not isinstance(target, (int, float)) or target <= cap_soc:
        return None
    soc05, soc22 = _snap(snapshots, "05:00", "soc"), _snap(snapshots, "22:00", "soc")
    reached = min(target, soc05) if soc05 is not None else target
    cut_soc = max(0.0, reached - cap_soc)
    lowest = shortfall_kwh = extra_gbp = None
    if soc22 is not None and late_load_kw is not None and battery_capacity_kwh > 0:
        end_soc = soc22 - late_load_kw * KPI_LATE_LOAD_HOURS / battery_capacity_kwh * 100
        socs = [s for s in (_snap(snapshots, t, "soc") for t in ("05:00", "16:00", "19:00")) if s is not None]
        lowest = min([*socs, end_soc])
        headroom = max(0.0, lowest - BATTERY_LOW_SOC)
        shortfall_kwh = max(0.0, cut_soc - headroom) / 100 * battery_capacity_kwh
        day, offpeak = prices_pence.get("day"), prices_pence.get("offpeak")
        if day is not None and offpeak is not None:
            extra_gbp = shortfall_kwh * (day - offpeak) / 100
    return {
        "target_soc": target,
        "cap_soc": cap_soc,
        "cut_kwh": round(cut_soc / 100 * battery_capacity_kwh, 2),
        "lowest_soc": round_or_none(lowest, 1),
        "shortfall_kwh": round_or_none(shortfall_kwh),
        "extra_cost_gbp": round_or_none(extra_gbp),
    }


def week_kpis(days: list[dict[str, Any]]) -> dict[str, float | None]:
    """Weekly roll-up: kWh/£ fields summed, self-sufficiency averaged over days that have it."""
    pct = ("self_sufficiency_pct", "grid_independence_pct")
    out = {f"week_{key}": sum_field(days, key) for key in DAY_KPI_FIELDS if key not in pct}
    for key in pct:
        vals = [d[key] for d in days if d.get(key) is not None]
        out[f"week_{key}"] = round(sum(vals) / len(vals), 1) if vals else None
    ss = [d["self_sufficiency_pct"] for d in days if d.get("self_sufficiency_pct") is not None]
    out["week_kpi_days"] = len(ss)
    return out


def sum_field(days: list[dict[str, Any]], key: str) -> float | None:
    """Sum `key` across days that have it, rounded to 2dp; None if no day does."""
    values = [d[key] for d in days if d.get(key) is not None]
    return round(sum(values), 2) if values else None


def no_battery_baseline(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    wear_pence: float | None = None,
    free_credit: dict[str, tuple[float, float]] | None = None,
) -> dict[str, float | None]:
    """What 00:00-22:00 cost with the battery vs without it, from the meter snapshots.

    A free event (`free_credit`) costs nothing: its import is left out of the
    actual cost, and its house load out of the no-battery cost.

    Per Flux segment: solar = load − import + export + battery gain (night
    segments have none). Without a battery the house uses that solar
    directly, imports the rest at the band price and exports the surplus.
    Actual = metered import − export at the same prices. Saving = baseline −
    actual − wear on the kWh the battery took in. Coarse bands overstate
    direct solar use, so the saving is conservative. Any missing snapshot or
    needed price → all None (the day is excluded, never 0).
    """
    empty = {"no_battery_gbp": None, "with_battery_gbp": None, "battery_saving_gbp": None, "battery_cycled_kwh": None}
    if battery_capacity_kwh <= 0:
        return empty
    baseline = actual = cycled = 0.0
    for start, end, band, sunny in _BASELINE_SEGMENTS:
        load = _seg_value(snapshots, start, end, "load_kwh")
        imp = _seg_value(snapshots, start, end, "grid_import_kwh")
        exp = _seg_value(snapshots, start, end, "grid_export_kwh")
        if load is None or imp is None or exp is None:
            return empty
        soc_a, soc_b = _snap(snapshots, start, "soc"), _snap(snapshots, end, "soc")
        gain = None if soc_a is None or soc_b is None else (soc_b - soc_a) / 100 * battery_capacity_kwh
        if sunny:
            if gain is None:
                return empty
            solar = max(0.0, load - imp + exp + gain)
        else:
            solar = 0.0
        if gain is not None and gain > 0:
            cycled += gain
        direct = min(solar, load)
        imp_price = prices_pence.get(band)
        exp_price = price_at(export_bands, hhmm_to_minutes(start))
        parts = (
            _pence(load - direct - _credit(free_credit, start, 1), imp_price), _pence(solar - direct, exp_price),
            _pence(imp - _credit(free_credit, start), imp_price), _pence(exp, exp_price),
        )
        if any(p is None for p in parts):
            return empty
        baseline += parts[0] - parts[1]
        actual += parts[2] - parts[3]
    wear = cycled * (wear_pence or 0.0)
    return {
        "no_battery_gbp": round(baseline / 100, 2),
        "with_battery_gbp": round(actual / 100, 2),
        "battery_saving_gbp": round((baseline - actual - wear) / 100, 2),
        "battery_cycled_kwh": round(cycled, 2),
    }


def battery_value_summary(days: list[dict[str, Any]]) -> dict[str, Any]:
    """{"saving_gbp", "days"} over days with a `battery_saving_gbp`; saving None when none has one."""
    values = [d["battery_saving_gbp"] for d in days if isinstance(d.get("battery_saving_gbp"), (int, float))]
    return {"saving_gbp": round(sum(values), 2) if values else None, "days": len(values)}


# Shadow go-live scorecard (phase 41): read-only, never enables a *_live option.
SCORECARD_WINDOW_DAYS = 30

SCORECARD_MIN_DAYS = 7

SCORECARD_MIN_CHANGED = 3

SCORECARD_HARM_GBP = 0.5

SCORECARD_SILENT_DAYS = 14

SCORECARD_ERROR_GAIN = 0.05  # weighted correction must beat the live factor by 5%

SCORECARD_ERROR_LOSS = 0.10  # ...and is harmful if 10% worse


def _in_window(records: list[dict[str, Any]], record_type: str, today: str, days: int) -> list[dict[str, Any]]:
    """``record_type`` records dated within the ``days`` ending ``today`` (ISO strings), one per date (last wins)."""
    try:
        floor = (date.fromisoformat(today) - timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for rec in records:
        d = rec.get("date")
        if rec.get("type") == record_type and isinstance(d, str) and floor < d <= today:
            by_date[d] = rec
    return [by_date[d] for d in sorted(by_date)]


def _score(days: int, changed: int, delta_gbp: float | None, dates: list[str], note: str = "") -> dict[str, Any]:
    """Verdict from record count, decisions changed and £ delta (positive = shadow would have helped).

    no_records: nothing logged. harmful: delta below -£0.50. ready: enough
    changed days with a positive delta. Otherwise not_yet (too few days, no
    £ estimate, or no change). Conservative by design.
    """
    if days == 0:
        verdict = "no_records"
    elif delta_gbp is not None and delta_gbp <= -SCORECARD_HARM_GBP:
        verdict = "harmful"
    elif days >= SCORECARD_MIN_DAYS and changed >= SCORECARD_MIN_CHANGED and delta_gbp is not None and delta_gbp > 0:
        verdict = "ready"
    else:
        verdict = "not_yet"
    return {
        "verdict": verdict, "days": days, "changed": changed, "delta_gbp": round_or_none(delta_gbp),
        "evidence": dates[-5:], "note": note,
    }


def _sum_or_none(values: list[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) if nums else None


def shadow_scorecard(
    records: list[dict[str, Any]], today: str, window_days: int = SCORECARD_WINDOW_DAYS
) -> dict[str, dict[str, Any]]:
    """Score each shadow feature against logged outcomes over the last ``window_days``.

    ``records`` are raw JSONL records. Returns {feature: {verdict, days,
    changed, delta_gbp, evidence, note}}; delta_gbp is None where no £
    counterfactual exists (never 0). A feature with no records in
    SCORECARD_SILENT_DAYS reports ``no_records`` so silent failures show.
    """
    def win(kind: str, days: int = window_days) -> list[dict[str, Any]]:
        return _in_window(records, kind, today, days)

    out: dict[str, dict[str, Any]] = {}
    plans = win("import_plan")
    # 15 import feedback: days the feedback would have moved the target.
    moved = [p for p in plans if p.get("import_feedback_adjustment") not in (None, 0)]
    out["import_feedback"] = _score(
        len([p for p in plans if "import_feedback_adjustment" in p]), len(moved), None,
        [p["date"] for p in moved], "no £ counterfactual; review the adjustment dates",
    )
    # 17 peak export: income minus replacement cost on days it would have exported.
    exports = win("peak_export")
    sold = [r for r in exports if r.get("decision") == "export"]
    out["peak_export"] = _score(
        len(exports),
        len(sold), _sum_or_none([r.get("gain_gbp") for r in sold]) if sold else None, [r["date"] for r in sold],
    )
    # 30 peak cover: day-rate top-up vs peak-rate purchase.
    covers = win("peak_cover")
    topped = [r for r in covers if r.get("decision") in ("top_up", "hold")]
    out["peak_cover"] = _score(
        len(covers),
        len(topped), _sum_or_none([r.get("gain_gbp") for r in topped]) if topped else None, [r["date"] for r in topped],
    )
    # 85% cap: positive extra cost is a loss, so the delta is its negative.
    caps = [k for k in win("day_kpis") if isinstance(k.get("soc_cap_shadow"), dict)]
    extra = _sum_or_none([k["soc_cap_shadow"].get("extra_cost_gbp") for k in caps])
    out["soc_cap_85"] = _score(
        len(caps), len([k for k in caps if (k["soc_cap_shadow"].get("extra_cost_gbp") or 0) > 0]),
        None if extra is None else -extra, [k["date"] for k in caps],
        "cap saves nothing modelled, so any extra cost counts against it",
    )
    # 20 weighted correction: mean absolute forecast error vs actual solar.
    actuals = {a["date"]: a for a in win("day_actuals")}
    live_err: list[float] = []
    weighted_err: list[float] = []
    dates: list[str] = []
    for p in plans:
        actual = (actuals.get(p["date"]) or {}).get("actual_solar_kwh")
        raw, live, weighted = p.get("raw_forecast_kwh"), p.get("forecast_correction_factor"), p.get("forecast_correction_weighted")
        if None in (actual, raw, live, weighted):
            continue
        live_err.append(abs(raw * live - actual))
        weighted_err.append(abs(raw * weighted - actual))
        dates.append(p["date"])
    score = _score(len(dates), 0, None, dates, "mean absolute error vs live factor")
    if len(dates) >= SCORECARD_MIN_DAYS and sum(live_err) > 0:
        ratio = sum(weighted_err) / sum(live_err)
        score["changed"] = len(dates)
        score["delta_gbp"] = None
        score["error_ratio"] = round(ratio, 3)
        score["verdict"] = "ready" if ratio <= 1 - SCORECARD_ERROR_GAIN else "harmful" if ratio >= 1 + SCORECARD_ERROR_LOSS else "not_yet"
    out["weighted_correction"] = score
    # 82 weather bins: mean absolute P50 error with the cloud bucket vs without it (shadow, no £ counterfactual).
    base_err: list[float] = []
    cloud_err: list[float] = []
    cloud_dates: list[str] = []
    for p in plans:
        actual = (actuals.get(p["date"]) or {}).get("actual_solar_kwh")
        base = (p.get("forecast_quantiles") or {}).get("p50_kwh")
        binned = (p.get("forecast_quantiles_cloud") or {}).get("p50_kwh")
        if None in (actual, base, binned):
            continue
        base_err.append(abs(base - actual))
        cloud_err.append(abs(binned - actual))
        cloud_dates.append(p["date"])
    score = _score(len(cloud_dates), 0, None, cloud_dates, "mean absolute P50 error, cloud bins vs band x season")
    if len(cloud_dates) >= SCORECARD_MIN_DAYS and sum(base_err) > 0:
        ratio = sum(cloud_err) / sum(base_err)
        score["changed"] = len(cloud_dates)
        score["delta_gbp"] = None
        score["error_ratio"] = round(ratio, 3)
        score["verdict"] = "ready" if ratio <= 1 - SCORECARD_ERROR_GAIN else "harmful" if ratio >= 1 + SCORECARD_ERROR_LOSS else "not_yet"
    out["weather_bins"] = score
    # 86 reserve hold: days that bought day-rate import with charge still in the battery (shadow, no £ counterfactual).
    holds = win("hourly_profile")
    flagged = [r for r in holds if (r.get("above_floor_day_import_kwh") or 0) >= RESERVE_HOLD_MIN_KWH]
    out["reserve_hold"] = _score(
        len(holds), len(flagged), None, [r["date"] for r in flagged],
        "day-rate import bought with charge above the floor; review the dates",
    )
    # 64 newsvendor ratio: asymmetric p-cost of the solar-scale error, live factor vs newsvendor ratio.
    nv_live: list[float] = []
    nv_shadow: list[float] = []
    nv_dates: list[str] = []
    for p in plans:
        actual = (actuals.get(p["date"]) or {}).get("actual_solar_kwh")
        raw, live = p.get("raw_forecast_kwh"), p.get("forecast_correction_factor")
        ratio, under, over = p.get("newsvendor_ratio"), p.get("newsvendor_under_p"), p.get("newsvendor_over_p")
        if None in (actual, raw, live, ratio, under, over) or raw <= 0.5:
            continue
        true = actual / raw
        # A scale above the true ratio under-buys (dear); below it over-buys.
        nv_live.append(under * max(0.0, live - true) + over * max(0.0, true - live))
        nv_shadow.append(under * max(0.0, ratio - true) + over * max(0.0, true - ratio))
        nv_dates.append(p["date"])
    nv_score = _score(len(nv_dates), 0, None, nv_dates, "asymmetric solar-scale error cost vs live factor")
    if len(nv_dates) >= SCORECARD_MIN_DAYS and sum(nv_live) > 0:
        nv_ratio = sum(nv_shadow) / sum(nv_live)
        nv_score["changed"] = len(nv_dates)
        nv_score["delta_gbp"] = None
        nv_score["error_ratio"] = round(nv_ratio, 3)
        nv_score["verdict"] = (
            "ready" if nv_ratio <= 1 - SCORECARD_ERROR_GAIN
            else "harmful" if nv_ratio >= 1 + SCORECARD_ERROR_LOSS else "not_yet"
        )
    out["newsvendor_target"] = nv_score
    # 23 gentle charge: nights it was applied (no £ counterfactual).
    gentle = [p for p in plans if p.get("gentle_charge_applied")]
    out["gentle_charge"] = _score(
        len([p for p in plans if "gentle_charge_applied" in p]), len(gentle), None, [p["date"] for p in gentle],
        "no £ counterfactual; check the charge-rate log",
    )
    # 39 multi-day: days where the shadow plan differed from the live plan.
    multi = win("multi_day_plan")
    differs = [m for m in multi if m.get("changes_target") or m.get("changes_peak")]
    out["multi_day"] = _score(len(multi), len(differs), None, [m["date"] for m in differs], "needs a ≥2 day low-solar run")
    # 34 capacity: latest estimate present and sampled.
    caps_est = win("capacity_estimate", 70)
    latest = caps_est[-1] if caps_est else {}
    est = latest.get("estimate_kwh")
    out["capacity"] = _score(len(caps_est), 0, None, [c["date"] for c in caps_est], "monthly estimate")
    if est is not None:
        out["capacity"]["verdict"] = "ready"
        out["capacity"]["estimate_kwh"] = est
        out["capacity"]["pct_of_configured"] = latest.get("pct_of_configured")
    # Silent-failure check: expected daily records absent for SCORECARD_SILENT_DAYS.
    for name, kind in (("peak_export", "peak_export"), ("peak_cover", "peak_cover")):
        if not win(kind, SCORECARD_SILENT_DAYS):
            out[name]["verdict"] = "no_records"
    return out


def scorecard_summary(card: dict[str, dict[str, Any]]) -> str:
    """One short human line per feature for the monthly notification."""
    lines = []
    for name, s in card.items():
        delta = "" if s.get("delta_gbp") is None else f", £{s['delta_gbp']:+.2f}"
        lines.append(f"{name}: {s['verdict'].replace('_', ' ')} ({s['changed']}/{s['days']} days{delta})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Phase 45: nightly data-integrity audit                                       #
# --------------------------------------------------------------------------- #

AUDIT_DAYS = 14

AUDIT_DAILY_TYPES = ("import_plan", "morning_state", "day_actuals", "day_kpis")

AUDIT_COST_SETTLE_DAYS = 2  # daily_cost can lag Octopus by a day or two; younger dates are not flagged

AUDIT_DEDUP_TYPES = AUDIT_DAILY_TYPES + ("charge_outcome", "peak_export", "peak_cover", "meter_snapshot")

AUDIT_SOLAR_MIN_KWH = 5.0  # a zero KPI against this much solar is a contradiction


def audit_days(
    records: list[dict[str, Any]],
    today: date,
    days: int = AUDIT_DAYS,
    expect_cost: bool = False,
) -> list[dict[str, str]]:
    """Self-audit of the last `days` complete days of JSONL records. Pure; never rewrites anything.

    Returns issue dicts {kind, type, date, detail}, sorted by date then kind:
    - missing: a daily record type absent for a date (only dates from the first day with
      any record, so a fresh install is not flagged for history it never had; `daily_cost`
      only when `expect_cost` and the date is past the settle lag);
    - duplicate: a per-day record type logged twice for the same date (and time);
    - contradiction: self_sufficiency 0.0 against meaningful solar, or a 22:00 load of 0
      with solar produced (the zero-actuals guard should have kept it out).
    Today is excluded: its 22:00 records do not exist yet.
    """
    window = {(today - timedelta(days=n)).isoformat() for n in range(1, days + 1)}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    first_seen: str | None = None
    for rec in records:
        rtype, rdate = rec.get("type"), rec.get("date")
        if not rtype or not isinstance(rdate, str):
            continue
        by_key.setdefault((rtype, rdate), []).append(rec)
        if first_seen is None or rdate < first_seen:
            first_seen = rdate
    issues: list[dict[str, str]] = []
    if first_seen is None:
        return issues
    for d in sorted(window):
        if d < first_seen:
            continue
        for rtype in AUDIT_DAILY_TYPES:
            if (rtype, d) not in by_key:
                issues.append({"kind": "missing", "type": rtype, "date": d, "detail": f"no {rtype} record"})
        if expect_cost and d <= (today - timedelta(days=AUDIT_COST_SETTLE_DAYS)).isoformat():
            cost = by_key.get(("daily_cost", d))
            if not cost or not any(c.get("actual_import_cost_gbp") is not None for c in cost):
                issues.append({"kind": "missing", "type": "daily_cost", "date": d, "detail": "no settled import cost"})
        for rtype in AUDIT_DEDUP_TYPES:
            recs = by_key.get((rtype, d), [])
            times = [r.get("time") for r in recs]
            if len(times) != len(set(times)):
                issues.append({"kind": "duplicate", "type": rtype, "date": d, "detail": f"{len(recs)} records"})
        actuals = (by_key.get(("day_actuals", d)) or [{}])[0]
        kpis = (by_key.get(("day_kpis", d)) or [{}])[0]
        solar, load = _num(actuals.get("actual_solar_kwh")), _num(actuals.get("day_load_kwh"))
        if solar is not None and solar >= AUDIT_SOLAR_MIN_KWH:
            if _num(kpis.get("self_sufficiency_pct")) == 0:
                issues.append({
                    "kind": "contradiction", "type": "day_kpis", "date": d,
                    "detail": f"self-sufficiency 0% with {solar:g} kWh of solar",
                })
            if load == 0:
                issues.append({
                    "kind": "contradiction", "type": "day_actuals", "date": d,
                    "detail": f"day load 0 kWh with {solar:g} kWh of solar",
                })
    return sorted(issues, key=lambda i: (i["date"], i["kind"], i["type"]))


def audit_summary(issues: list[dict[str, str]]) -> dict[str, Any]:
    """Counts and per-class date lists for the sensor and Repairs issues."""
    classes: dict[str, list[str]] = {}
    for issue in issues:
        classes.setdefault(issue["kind"], []).append(f"{issue['date']} {issue['type']}")
    return {"count": len(issues), "classes": classes}


# --------------------------------------------------------------------------- #
# Phase 47: KPI sensors                                                        #
# --------------------------------------------------------------------------- #

# sensor key → (day_kpis field, name, unit, kind); kind "money" is a daily £ total, else a measurement.
KPI_SENSORS = (
    ("kpi_avoidable_import", "avoidable_import_gbp", "Avoidable import today", "GBP", "money"),
    ("kpi_export_peak", "export_peak_gbp", "Peak export income today", "GBP", "money"),
    ("kpi_self_sufficiency", "self_sufficiency_pct", "Self-sufficiency today", "%", "measurement"),
    ("kpi_unused_charge", "unused_charge_kwh", "Unused charge today", "kWh", "measurement"),
    ("kpi_over_limit_import", "over_limit_import_kwh", "Over-limit import today", "kWh", "measurement"),
)


def kpi_sensor_values(record: dict[str, Any] | None) -> dict[str, float | None]:
    """Latest 22:00 day_kpis record → {sensor key: value}. Missing or non-numeric is None, never 0."""
    rec = record if isinstance(record, dict) else {}
    out: dict[str, float | None] = {}
    for key, field_name, _name, _unit, _kind in KPI_SENSORS:
        value = rec.get(field_name)
        out[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return out


RESERVE_HOLD_MIN_KWH = 0.5  # a day with at least this much above-floor day import is flagged in the scorecard


def ledger_point(date: str, health) -> dict | None:
    """The compact per-day history entry kept in state for the trend."""
    if not isinstance(health, dict) or health.get("soh_pct") is None:
        return None
    return {
        "date": date, "soh_pct": health["soh_pct"], "cycles_max": health.get("cycles_max"),
        "discharge_total_kwh": health.get("discharge_total_kwh"),
    }
