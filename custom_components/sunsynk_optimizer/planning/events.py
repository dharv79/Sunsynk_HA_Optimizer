# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Free-electricity and flex events, inverter event/notice parsing, outage tracking."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .core import (
    PEAK_EXPORT_MIN_KWH,
    PEAK_START_MINUTES,
    ROUND_TRIP_EFFICIENCY,
    _BASELINE_SEGMENTS,
    _as_aware,
    _is_number,
    hhmm_to_minutes,
)
from .peak import (
    peak_export_plan,
)
from .redact import (
    url_segment,
)
from .reports import (
    meter_delta,
)


CHEAP_WINDOW_END = "05:00"

# Never sell the battery below this SOC ahead of a free electricity event,
# even if the refillable-floor maths would allow it.
FREE_EVENT_MIN_FLOOR_SOC = 40

# Longest sell window before a free period (16/10/2026: was the free period's
# own length, which left a full battery unable to take the free import).
FREE_EVENT_MAX_SELL_HOURS = 4.0

FREE_EVENT_TREND_MINUTES = 30  # SOC trend window (the battery's real charge rate)

FREE_EVENT_TREND_MIN_MINUTES = 5  # shorter spans are too noisy at 1% SOC steps

FREE_EVENT_SELL_MARGIN_HOURS = 0.25


@dataclass
class FreeEventPlan:
    """Outcome of plan_free_event — see phases/10-free-electricity-event.md."""

    sell_start: datetime
    sell_end: datetime
    sell_floor_soc: int
    expected_export_kwh: float
    expected_refill_kwh: float
    skip_sell: bool


def plan_free_event(
    free_start: datetime,
    free_end: datetime,
    soc: float,
    capacity_kwh: float,
    charge_rate_kw: float,
    export_rate_kw: float,
    max_sell_hours: float = FREE_EVENT_MAX_SELL_HOURS,
) -> FreeEventPlan:
    """Plan the sell-before / refill-during shape of a free electricity event.

    Sells down to a "refillable floor" — the SOC that free-period charging at
    charge_rate_kw can top back up to 100% — never below FREE_EVENT_MIN_FLOOR_SOC.
    The sell window ends at free_start and is only as long as needed at
    export_rate_kw, capped at ``max_sell_hours``. This is the estimate shown
    when the event is scheduled; the real sell start is decided live on the day
    (free_event_sell_decision). Nothing to sell → skip_sell=True.
    """
    free_hours = elapsed_seconds(free_end, free_start) / 3600
    refill_kwh = charge_rate_kw * free_hours
    raw_floor = 100 - (refill_kwh / capacity_kwh * 100 if capacity_kwh > 0 else 0)
    sell_floor_soc = int(max(FREE_EVENT_MIN_FLOOR_SOC, min(100, round(raw_floor))))

    energy_to_sell_kwh = (soc - sell_floor_soc) / 100 * capacity_kwh
    skip_sell = energy_to_sell_kwh <= 0.05
    sell_hours = 0.0
    expected_export_kwh = 0.0
    if not skip_sell and export_rate_kw > 0:
        sell_hours = min(energy_to_sell_kwh / export_rate_kw, max_sell_hours)
        expected_export_kwh = round(sell_hours * export_rate_kw, 2)

    if capacity_kwh > 0:
        soc_at_free_start = min(100.0, max(0.0, soc - expected_export_kwh / capacity_kwh * 100))
    else:
        soc_at_free_start = soc
    headroom_kwh = max(0.0, capacity_kwh - soc_at_free_start / 100 * capacity_kwh)
    expected_refill_kwh = round(min(refill_kwh, headroom_kwh), 2)

    sell_start = shift_hours(free_start, -sell_hours)

    return FreeEventPlan(
        sell_start=sell_start,
        sell_end=free_start,
        sell_floor_soc=sell_floor_soc,
        expected_export_kwh=expected_export_kwh,
        expected_refill_kwh=expected_refill_kwh,
        skip_sell=skip_sell,
    )


def elapsed_seconds(later: datetime, earlier: datetime) -> float:
    """Real seconds from ``earlier`` to ``later``. Aware datetimes are compared in UTC: Python
    subtracts two datetimes that share a tzinfo as wall-clock time, which is 1 h out across a
    clock change. Naive values fall back to plain subtraction."""
    if later.tzinfo is None or earlier.tzinfo is None:
        return (later - earlier).total_seconds()
    return (later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)).total_seconds()


def shift_hours(moment: datetime, hours: float) -> datetime:
    """``moment`` moved by ``hours`` of real time (negative = earlier), DST-safe, same tzinfo."""
    if moment.tzinfo is None:
        return moment + timedelta(hours=hours)
    return (moment.astimezone(timezone.utc) + timedelta(hours=hours)).astimezone(moment.tzinfo)


def free_event_watch_from(free_start: datetime) -> datetime:
    """When the live sell-start watch begins (the longest sell window before the free start)."""
    return shift_hours(free_start, -FREE_EVENT_MAX_SELL_HOURS)


def free_event_in_watch(now: datetime, free_start: datetime, watch_from: datetime | None = None) -> bool:
    """True only from ``watch_from`` until the free start; no sell may begin outside it."""
    start = watch_from or free_event_watch_from(free_start)
    return elapsed_seconds(now, start) >= 0 and elapsed_seconds(free_start, now) > 0


# Scheduling-time sell estimates; only meaningful once the live watch has started a sell.
FREE_EVENT_ESTIMATE_KEYS = ("sell_start", "sell_end", "skip_sell", "expected_export_kwh")


def free_event_sold(event: dict) -> bool:
    """True once the sell start has run (it always records ``soc_at_sell_start``)."""
    return "soc_at_sell_start" in event


def free_event_sell_status(event: dict) -> str | None:
    """Dashboard text for the sell: watching, selling, sold or not needed."""
    phase = event.get("phase")
    if not phase or not event.get("free_start"):
        return None
    if phase == "scheduled":
        watch_from = event.get("watch_from") or free_event_watch_from(
            datetime.fromisoformat(event["free_start"])
        ).isoformat()
        return f"Watching from {datetime.fromisoformat(watch_from):%a %H:%M}"
    if phase == "cancelled":
        return "Cancelled"
    if not free_event_sold(event) or not event.get("sell_start"):
        return "Not needed"
    sell_start = datetime.fromisoformat(event["sell_start"])
    return f"Selling since {sell_start:%H:%M}" if phase == "selling" else f"Sold from {sell_start:%H:%M}"


# Attributes the dashboard reads: always present (None when unknown) so Spook
# never flags them as unknown.
FREE_EVENT_ATTRIBUTE_KEYS = (
    "free_start", "free_end", "sell_status", "sell_floor_soc", "expected_export_kwh", "expected_refill_kwh",
)


def free_event_attributes(event: dict) -> dict:
    """Sensor attributes: no raw meters, no sell estimates until a sell has run, plus ``sell_status``."""
    hidden = () if free_event_sold(event) else FREE_EVENT_ESTIMATE_KEYS
    attrs = dict.fromkeys(FREE_EVENT_ATTRIBUTE_KEYS)
    attrs.update({k: v for k, v in event.items() if not k.startswith("meters_") and k not in hidden})
    status = free_event_sell_status(event)
    if status:
        attrs["sell_status"] = status
    return attrs


def soc_trend_kw(samples: list[tuple[datetime, float]], capacity_kwh: float, now: datetime) -> float | None:
    """Battery charge rate (kW, negative = discharging) from SOC samples in the last
    FREE_EVENT_TREND_MINUTES; None until they span FREE_EVENT_TREND_MIN_MINUTES."""
    recent = [(ts, soc) for ts, soc in samples if elapsed_seconds(now, ts) <= FREE_EVENT_TREND_MINUTES * 60]
    if len(recent) < 2:
        return None
    (t0, s0), (t1, s1) = recent[0], recent[-1]
    hours = elapsed_seconds(t1, t0) / 3600
    if hours * 60 < FREE_EVENT_TREND_MIN_MINUTES or capacity_kwh <= 0:
        return None
    return round((s1 - s0) / 100 * capacity_kwh / hours, 2)


def free_event_sell_decision(
    soc: float,
    floor_soc: float,
    capacity_kwh: float,
    trend_kw: float | None,
    export_rate_kw: float,
    hours_left: float,
    margin_hours: float = FREE_EVENT_SELL_MARGIN_HOURS,
) -> dict[str, Any]:
    """Whether to start selling now ahead of a free period.

    Projects the SOC at the free start from the measured charge trend (solar
    going into the battery, capped at 100%; a falling trend is ignored), then
    the hours needed to export down to the floor. Start once that, plus a
    margin, reaches the time left.
    """
    charge_kw = max(0.0, trend_kw or 0.0)
    projected_soc = min(100.0, soc + (charge_kw * hours_left / capacity_kwh * 100 if capacity_kwh > 0 else 0.0))
    energy_kwh = max(0.0, (projected_soc - floor_soc) / 100 * capacity_kwh)
    needed_hours = energy_kwh / export_rate_kw if export_rate_kw > 0 else None
    sell_now = (
        energy_kwh > 0.05 and needed_hours is not None and needed_hours + margin_hours >= hours_left
    )
    return {
        "sell_now": sell_now,
        "trend_kw": trend_kw,
        "projected_soc": round(projected_soc, 1),
        "energy_kwh": round(energy_kwh, 2),
        "needed_hours": round(needed_hours, 2) if needed_hours is not None else None,
        "hours_left": round(hours_left, 2),
    }


def free_event_target_cap(
    target_soc: int,
    event: dict[str, Any] | None,
    free_start: datetime | None,
    plan_date: date,
    load_kw: float | None = None,
    capacity_kwh: float | None = None,
) -> int:
    """01:55 target on a free-event day: the sell floor plus the house load until the free start.

    The floor is where the battery should be at the free start, not at 05:00:
    the house runs on it from the end of the cheap window until then, so that
    load is added (no solar credit; any surplus is sold by the live watch).
    Charging further would only be sold back at a lower export price. Applies
    when the event (still scheduled) starts on the plan date before the 16:00
    peak; otherwise unchanged.
    """
    event = event or {}
    floor = event.get("sell_floor_soc")
    if (
        event.get("phase") != "scheduled" or not _is_number(floor) or free_start is None
        or free_start.date() != plan_date or free_start.hour * 60 + free_start.minute >= PEAK_START_MINUTES
    ):
        return target_soc
    cap = int(floor)
    if _is_number(load_kw) and _is_number(capacity_kwh) and capacity_kwh > 0:
        cheap_end = hhmm_to_minutes(CHEAP_WINDOW_END)
        bridge_hours = max(0, free_start.hour * 60 + free_start.minute - cheap_end) / 60
        cap += math.ceil(max(0.0, load_kw) * bridge_hours / capacity_kwh * 100)
    return min(target_soc, min(100, cap))


def manual_free_event_error(
    changed: str, start: datetime, end: datetime, now: datetime, label: str = "Free event"
) -> str | None:
    """Validate a manual event pair after one field ('start' or 'end') was just set.

    The two fields are entered one at a time, so the field not being edited may
    still hold a stale value. Only the just-set field is judged: a start in the
    past is always an error, but a start at/after a stale end is silently
    accepted (the end is about to be set); an end at/before start is an error.
    Returns the message to notify, or None when the pair is fine or incomplete.
    `label` names the event in the message.
    """
    if changed == "start":
        if start <= now:
            return f"{label} start must be in the future."
        return None
    if end <= start:
        return f"{label} end must be after start."
    return None


def _gbp(kwh: float | None, pence: float | None) -> float | None:
    return round(kwh * pence / 100, 2) if _is_number(kwh) and _is_number(pence) else None


def free_event_result(event: dict[str, Any], meters_end: dict[str, Any] | None, soc_end: float | None,
                      import_pence: float | None, capacity_kwh: float | None = None) -> dict[str, Any]:
    """Measured outcome of a finished free-electricity event (debug report).

    `sold_kwh` is grid export from the sell start to the free start; `free_import_kwh`
    is grid import during the free period, valued at what it would normally have
    cost (`import_pence`). No sell ran → `sold_kwh` 0.0. Missing inputs → None.
    `sold_from_battery_kwh` is the SOC fall over the sell (needs `capacity_kwh`);
    `sold_from_solar_kwh` is the rest of `sold_kwh` (never below 0).
    """
    free_import = meter_delta(event.get("meters_at_free_start"), meters_end, "grid_import")
    sold = (
        meter_delta(event.get("meters_at_sell_start"), event.get("meters_at_free_start"), "grid_export")
        if free_event_sold(event) else 0.0
    )
    from_battery = from_solar = None
    if free_event_sold(event):
        s0, s1 = event.get("soc_at_sell_start"), event.get("soc_at_free_start")
        if s0 is not None and s1 is not None and capacity_kwh:
            from_battery = round(max(0.0, (s0 - s1) / 100 * capacity_kwh), 2)
            if sold is not None:
                from_solar = round(max(0.0, sold - from_battery), 2)
    else:
        from_battery = from_solar = 0.0
    return {
        "sold_kwh": sold,
        "sold_from_battery_kwh": from_battery,
        "sold_from_solar_kwh": from_solar,
        "free_import_kwh": free_import,
        "free_import_value_gbp": _gbp(free_import, import_pence),
        "soc_at_end": soc_end,
    }


def event_report(record_type: str, phase: str, event: dict[str, Any]) -> dict[str, Any]:
    """Debug-channel JSON line for a free event lifecycle point (raw meter readings dropped,
    and the scheduling-time sell estimates unless a sell ran)."""
    hidden = () if free_event_sold(event) else FREE_EVENT_ESTIMATE_KEYS
    return {
        "type": record_type, "phase": phase,
        **{k: v for k, v in event.items() if not k.startswith("meters_") and k not in hidden},
    }


def free_event_kpi_credit(
    event: dict[str, Any] | None, day: date, capacity_kwh: float
) -> dict[str, tuple[float, float]] | None:
    """A finished free event's import and house load on `day`, per KPI segment start.

    Split by time overlap with the 00:00-22:00 segments. House load = import −
    battery gain over the free period (0 when the SOCs are missing). None when
    there's no finished event with a measured import that day.
    """
    event = event or {}
    free_import = event.get("free_import_kwh")
    if event.get("phase") != "done" or not _is_number(free_import) or free_import <= 0:
        return None
    start = datetime.fromisoformat(event["free_start"])
    end = datetime.fromisoformat(event["free_end"])
    if start.date() != day or end <= start:
        return None
    soc_a, soc_b = event.get("soc_at_free_start"), event.get("soc_at_end")
    gain = (soc_b - soc_a) / 100 * capacity_kwh if _is_number(soc_a) and _is_number(soc_b) else free_import
    load = max(0.0, free_import - gain)
    start_min = start.hour * 60 + start.minute
    end_min = start_min + (end - start).total_seconds() / 60
    credit = {}
    for seg_start, seg_end, _band, _sunny in _BASELINE_SEGMENTS:
        overlap = min(end_min, hhmm_to_minutes(seg_end)) - max(start_min, hhmm_to_minutes(seg_start))
        if overlap > 0:
            share = overlap / (end_min - start_min)
            credit[seg_start] = (free_import * share, load * share)
    return credit or None


# --------------------------------------------------------------------------- #
# Phase 48: grid outage detection                                              #
# --------------------------------------------------------------------------- #

OUTAGE_MIN_LOAD_KW = 0.05  # below this the runtime estimate is meaningless (battery idle)

GRID_ONLINE_MIN_V = 180.0  # at or above: mains present

GRID_OFFLINE_MAX_V = 100.0  # below: mains lost (between the two: unknown, no change)


def grid_online_from_voltage(voltage: Any) -> bool | None:
    """Grid present from the live grid phase voltage (live sample: `grid_status` reads 0 with 240 V, so it is unusable).

    None for missing/unparseable or the 100-180 V dead band, which never opens or closes an outage.
    """
    try:
        v = float(voltage)
    except (TypeError, ValueError):
        return None
    if v >= GRID_ONLINE_MIN_V:
        return True
    if v < GRID_OFFLINE_MAX_V:
        return False
    return None


def outage_runtime_hours(soc: float | None, shutdown_soc: float, capacity_kwh: float, load_kw: float | None) -> float | None:
    """Hours the battery can carry `load_kw` down to `shutdown_soc`; None when an input is missing or the load is ~0."""
    if soc is None or load_kw is None or capacity_kwh <= 0 or load_kw < OUTAGE_MIN_LOAD_KW:
        return None
    return round(max(0.0, soc - shutdown_soc) / 100 * capacity_kwh / load_kw, 1)


def outage_track(outage: dict[str, Any], soc: float | None) -> dict[str, Any]:
    """Active outage dict with the SOC low point updated (unchanged when SOC is unknown)."""
    if soc is None:
        return outage
    low = outage.get("soc_low")
    return {**outage, "soc_low": soc if low is None else min(low, soc)}


def outage_record(outage: dict[str, Any], end_iso: str, soc_end: float | None, capacity_kwh: float) -> dict[str, Any]:
    """Closed-outage log fields. kWh served = start SOC less the low point, as battery energy; None if unknown."""
    start_soc, low = outage.get("soc_start"), outage.get("soc_low")
    served = None
    if isinstance(start_soc, (int, float)) and isinstance(low, (int, float)):
        served = round(max(0.0, start_soc - low) / 100 * capacity_kwh, 2)
    minutes = None
    try:
        minutes = round((datetime.fromisoformat(end_iso) - datetime.fromisoformat(outage["start"])).total_seconds() / 60)
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "start": outage.get("start"), "end": end_iso, "minutes": minutes,
        "soc_start": start_soc, "soc_low": low, "soc_end": soc_end, "battery_kwh_served": served,
    }


# --------------------------------------------------------------------------- #
# Phase 43: inverter alarm/event feed feasibility probe                        #
# --------------------------------------------------------------------------- #

EVENT_PROBE_ROUTES = (
    # Round 2 (04/11/2026): plant/{id}/events accepts `lan` but returns code 500 with only page/limit/lan,
    # so round 3 adds the date range and ids a typical events DTO would want.
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&startTime={start}&endTime={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&startDate={start}&endDate={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&beginTime={start}&endTime={today}&status=1",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&sn={sn}&startTime={start}&endTime={today}&type=1",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&date={today}",
    "/api/v1/plant/{plant_id}/events?page=1&limit=20&lan=en&plantId={plant_id}&startTime={start}&endTime={today}",
)


def event_probe_paths(inverter_sn: str, plant_id: str, today: str = "2026-01-01", start: str = "2025-12-01") -> list[str]:
    """Candidate read-only GET routes for the cloud's alarm/event list, with ids and dates filled in."""
    return [r.format(sn=url_segment(inverter_sn), plant_id=url_segment(plant_id), today=today, start=start) for r in EVENT_PROBE_ROUTES]


_EVENT_TEXT_KEYS = ("eventName", "name", "content", "message", "desc", "description", "alarmName", "title")

_EVENT_CODE_KEYS = ("code", "eventCode", "alarmCode", "errorCode", "id")

_EVENT_TIME_KEYS = ("startTime", "time", "occurTime", "createTime", "happenTime")

_EVENT_CATEGORY_WORDS = (
    ("battery", ("batt", "bms", "cell", "soc")),
    ("grid", ("grid", "mains", "ac ", "utility", "fac", "vac")),
    ("charge", ("charg", "overload", "over temp", "temperature", "fan")),
)


def _first_text(item: dict, keys) -> str | None:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def normalise_inverter_events(raw) -> list[dict] | None:
    """Tolerant parse of the Sunsynk realtime event list (phase 43). The healthy reply is an empty list.

    The shape of a live fault is not yet known, so each item is reduced to text/code/time/category with
    the raw item kept; None when the reply is not a list (never 0 or []: unknown is not 'no faults')."""
    if not isinstance(raw, list):
        return None
    events: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            item = {"content": str(item)}
        text = _first_text(item, _EVENT_TEXT_KEYS)
        code = _first_text(item, _EVENT_CODE_KEYS)
        when = _first_text(item, _EVENT_TIME_KEYS)
        lowered = (text or "").lower()
        category = next((c for c, words in _EVENT_CATEGORY_WORDS if any(w in lowered for w in words)), "other")
        events.append(
            {"key": f"{code}|{text}|{when}", "text": text, "code": code, "time": when, "category": category, "raw": item}
        )
    return events


def new_inverter_events(events: list[dict], seen: list[str]) -> list[dict]:
    """Events whose key has not been reported before (dedup across checks)."""
    known = set(seen or [])
    return [e for e in events if e["key"] not in known]


# Cloud notices feed (messageTd/messages), captured 04/11/2026: messageType 7 = grid lost, 2 = grid restored.
_GRID_NOTICE_TYPES = {7: "lost", 2: "restored"}


def _grid_notice_kind(record: dict) -> str | None:
    try:
        kind = _GRID_NOTICE_TYPES.get(int(record.get("messageType")))
    except (TypeError, ValueError):
        kind = None
    if kind:
        return kind
    text = str(record.get("description") or "").lower()
    if "disconnected from the grid" in text:
        return "lost"
    if "grid has been restored" in text:
        return "restored"
    return None


def normalise_grid_notices(records, inverter_sn: str | None = None) -> list[dict] | None:
    """Grid lost/restored notices from the Sunsynk cloud feed, oldest first (phase 43 follow-up).

    Keeps only records whose kind is known and whose `sn` matches (when both are set). None when the
    reply is not a list, so an unreadable feed is never read as 'no outages'."""
    if not isinstance(records, list):
        return None
    notices = []
    for record in records:
        if not isinstance(record, dict):
            continue
        kind = _grid_notice_kind(record)
        if kind is None or record.get("id") in (None, ""):
            continue
        sn = record.get("sn")
        if inverter_sn and sn and str(sn) != str(inverter_sn):
            continue
        notices.append({"id": str(record["id"]), "kind": kind, "time": record.get("createAt"), "sn": sn})
    return sorted(notices, key=lambda n: str(n["time"] or ""))


def new_grid_notices(notices: list[dict], seen: list[str]) -> list[dict]:
    """Notices whose id has not been logged before."""
    known = set(seen or [])
    return [n for n in notices if n["id"] not in known]


_COUNT_KEYS = ("fault", "warning", "offline", "protect", "normal", "total")


def normalise_inverter_counts(data) -> dict | None:
    """Account-wide inverter status counts (`/api/v1/inverters/count`); None if unreadable.

    Missing or non-numeric keys stay None (never 0)."""
    if not isinstance(data, dict):
        return None
    counts = {}
    for key in _COUNT_KEYS:
        try:
            counts[key] = int(data[key]) if data.get(key) is not None else None
        except (TypeError, ValueError):
            counts[key] = None
    return counts if any(v is not None for v in counts.values()) else None


def inverter_fault_flag(active_events, counts, health_faults=None) -> bool | None:
    """True when the realtime list has events, the cloud counts a fault/protect state, or a register
    fault word is set (health_faults: bool | None from health_fault_flag).

    False only when at least one source was read and shows nothing; None when none was read."""
    signals = [] if health_faults is None else [bool(health_faults)]
    if active_events is not None:
        signals.append(bool(active_events))
    if counts:
        known = [counts.get(k) for k in ("fault", "protect") if counts.get(k) is not None]
        if known:
            signals.append(any(v > 0 for v in known))
    return any(signals) if signals else None


# Flexibility-market export events (phase 77). Shadow only: what the battery could safely offer
# a paid export event, logged; nothing is exported.
FLEX_DEFAULT_HOURS = 1.0  # an on/off entity with no end time

FLEX_MAX_HOURS = 4.0


def flex_event_window(attrs: Any, now: datetime, tz: Any = None) -> tuple[str, float]:
    """(event start HH:MM, hours left) from a calendar entity's start_time/end_time attributes.

    An on/off entity has neither: it starts now and lasts FLEX_DEFAULT_HOURS.
    Hours are capped at FLEX_MAX_HOURS and never below half an hour.
    """
    attrs = attrs if isinstance(attrs, dict) else {}
    tz = tz or now.tzinfo
    start = _as_aware(attrs.get("start_time"), tz)
    end = _as_aware(attrs.get("end_time"), tz)
    start_local = (start.astimezone(tz) if tz is not None else start) if start is not None else now
    hours = (end - now).total_seconds() / 3600 if end is not None else FLEX_DEFAULT_HOURS
    hours = max(0.5, min(FLEX_MAX_HOURS, hours))
    return f"{start_local.hour:02d}:{start_local.minute:02d}", round(hours, 2)


def flex_event_offer(
    soc: float | None,
    reserve_soc: int,
    battery_capacity_kwh: float,
    hours: float,
    house_load_kw: float,
    price_pence: float | None,
    offpeak_pence: float | None,
    export_rate_kw: float,
    wear_pence: float | None = None,
    slots_held: bool = False,
) -> dict[str, Any]:
    """kWh the battery could sell into a paid export event without breaking the evening reserve.

    Reuses the peak export floor (`peak_export_plan`): reserve + the house
    load over the event + margin, and the same worth-it test against the
    off-peak refill cost after round-trip losses and wear. The offer is also
    capped by the export rate over the event. `decision` is offer /
    no_surplus / not_worth_it / no_price / no_soc / slots_held (a free event
    owns the slots).
    """
    empty = {"offer_kwh": 0.0, "value_gbp": None, "gain_gbp": None, "floor_soc": None}
    if slots_held:
        return {"decision": "slots_held", **empty}
    if not _is_number(soc):
        return {"decision": "no_soc", **empty}
    need_soc = max(0.0, house_load_kw) * hours / battery_capacity_kwh * 100 if battery_capacity_kwh > 0 else 0.0
    plan = peak_export_plan(
        soc, reserve_soc, battery_capacity_kwh, price_pence, offpeak_pence, wear_pence=wear_pence, peak_need_soc=need_soc
    )
    decision = {"export": "offer"}.get(plan["decision"], plan["decision"])
    if decision != "offer":
        return {"decision": decision, **empty, "floor_soc": plan["target_soc"]}
    offer = min(plan["export_kwh"], max(0.0, export_rate_kw) * hours)
    if offer < PEAK_EXPORT_MIN_KWH:
        return {"decision": "no_surplus", **empty, "floor_soc": plan["target_soc"]}
    value = offer * price_pence / 100
    gain = value - offer * (offpeak_pence / ROUND_TRIP_EFFICIENCY + (wear_pence or 0.0)) / 100
    return {
        "decision": "offer",
        "offer_kwh": round(offer, 2),
        "value_gbp": round(value, 2),
        "gain_gbp": round(gain, 2),
        "floor_soc": plan["target_soc"],
    }
