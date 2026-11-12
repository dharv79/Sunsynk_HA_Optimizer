# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Shared maths, time helpers and cost helpers used by every planning module."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any


# Battery limits: the inverter stops discharging at the bottom and starts slowing
# discharge at the low mark, so plans size above the bottom and aim to stay above low.
BATTERY_BOTTOM_SOC = 20

BATTERY_LOW_SOC = 25

MEASURED_RATE_NIGHTS = 14

_NO_DEFAULT: Any = object()


def percentile(values: list[float], pct: float, default: Any = _NO_DEFAULT) -> float:
    """pct-th percentile (0-100), linear interpolation between ranks; the one copy.

    An empty list returns ``default`` when given, else raises ValueError.
    """
    if not values:
        if default is _NO_DEFAULT:
            raise ValueError("percentile of an empty list")
        return default
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def median(values: list[float]) -> float:
    """Median (mean of the middle two for an even count); raises ValueError when empty."""
    if not values:
        raise ValueError("median of an empty list")
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


_percentile = percentile

# Share of imported AC kWh that lands as battery SOC at the nameplate charge
# rate (inverter conversion plus cell losses).
CHARGE_EFFICIENCY = 0.92

# Phase 23: gentler charging — the lowest grid-charge current that still
# reaches the target by the end of the 02:00-05:00 cheap window.
CHEAP_WINDOW_HOURS = 3.0

PEAK_START_MINUTES = 16 * 60  # Flux peak band 16:00-19:00

PEAK_END_MINUTES = 19 * 60

DEFAULT_BATTERY_VOLTAGE = 51.2


def minutes_to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hhmm_to_minutes(hhmm: str) -> int | None:
    """Minutes after midnight for "HH:MM" (or "HH:MM:SS"), or None when malformed."""
    try:
        hours, minutes = str(hhmm).split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except (ValueError, TypeError):
        return None


CHARGE_WATCHDOG_MINUTES = 2 * 60 + 20  # 20 min into the 02:00 import window

CHARGE_WATCHDOG_RECHECK_MINUTES = 15

# Phase 14: cumulative daily-meter snapshots at the Flux band edges. The
# SolarSynkV3 daily totals reset at midnight, so 22:00 closes the KPI day
# (22:00-24:00 is not covered, same as day_actuals).
KPI_RESERVE_SOC = BATTERY_BOTTOM_SOC


def _snap(snapshots: dict[str, dict[str, Any]], time: str, key: str) -> float | None:
    value = (snapshots.get(time) or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _snap_delta(snapshots: dict[str, dict[str, Any]], start: str, end: str, key: str) -> float | None:
    """Meter increase between two snapshots; None if either is missing or it went backwards."""
    a, b = _snap(snapshots, start, key), _snap(snapshots, end, key)
    if a is None or b is None or b < a:
        return None
    return b - a


IMPORT_CAUSES = ("free_event", "grid_charge", "load_over_limit", "battery_floor", "slot_hold", "unknown")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


_median = median

# Phase 16: keep enough at 19:00 to run the house to the 02:00 off-peak start.
EVENING_RESERVE_HOURS = 7.0

TRIM_FROM_MINUTES = 5 * 60  # end of the cheap import window

PEAK_EXPORT_MIN_KWH = 0.5  # below this it isn't worth an API write

ROUND_TRIP_EFFICIENCY = 0.85

PEAK_COVER_MARGIN = 1.1

PEAK_COVER_FROM_MINUTES = 12 * 60

PEAK_COVER_PEAK_START_MINUTES = PEAK_START_MINUTES

PEAK_COVER_LAST_LOG_MINUTES = 15 * 60 + 30

DISCHARGE_EFFICIENCY = 0.92  # battery -> house; ~ CHARGE_EFFICIENCY, so round trip ~ ROUND_TRIP_EFFICIENCY


def net_cost_gbp(import_cost: float | None, export_income: float | None, gas_cost: float | None) -> float | None:
    """import - export + gas, or None if any input is missing (never treat missing as £0)."""
    if import_cost is None or export_income is None or gas_cost is None:
        return None
    return round(import_cost - export_income + gas_cost, 2)


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


# Paired-day history to load for a trailing week: the oldest day's 01:55
# import_plan is ~7 d 16 h old at the Sunday 18:00 digest, and
# load_paired_days filters by recorded_at, so 7 would drop it.
WEEK_HISTORY_DAYS = 9


def pack_lines(lines: list[str], limit: int = 3500) -> list[list[str]]:
    """Pack whole lines into as few messages as fit under `limit` chars (phase 89).

    A single line over the limit goes alone, never cut.
    """
    packs: list[list[str]] = []
    size = 0
    for line in lines:
        if not line:
            continue
        add = len(line) + (1 if packs and packs[-1] else 0)
        if packs and size + add <= limit:
            packs[-1].append(line)
            size += add
        else:
            packs.append([line])
            size = len(line)
    return packs or [[""]]


def trailing_week(today: date) -> tuple[str, str]:
    """ISO (start, end) of the 7 complete days ending yesterday.

    Today is excluded because its day_actuals isn't logged until 22:00.
    """
    return (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()


def days_in_period(days: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    """Paired days whose ISO `date` falls within [start, end]."""
    return [d for d in days if start <= str(d.get("date", "")) <= end]


# The sell start is decided live: from FREE_EVENT_MAX_SELL_HOURS before the free
# start, every FREE_EVENT_WATCH_MINUTES, from the SOC and its recent trend.
FREE_EVENT_WATCH_MINUTES = 10


def state_fields_changed(current: Any, updates: dict[str, Any]) -> bool:
    """True when applying ``updates`` to ``current`` would change anything (phase 67).

    A dict/list/set that is the very same object counts as changed: it may have been mutated
    in place before being passed back. An unknown field is ignored, as update_state does."""
    for key, value in updates.items():
        if not hasattr(current, key):
            continue
        old = getattr(current, key)
        if old != value or (old is value and isinstance(value, (dict, list, set))):
            return True
    return False


def _as_aware(value: Any, tz) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


DAYTIME_IMPORT_AWAY_THRESHOLD_W = 750.0

DAYTIME_IMPORT_FROM_HOUR = 5

DAYTIME_IMPORT_UNTIL_HOUR = 16

VOLTAGE_DERATE_PERIOD_MIN = 30

# (start "HH:MM", end "HH:MM", rate key); an end before its start wraps midnight.
TARIFF_IMPORT_BANDS: dict[str, list[tuple[str, str, str]]] = {
    "go": [("00:30", "05:30", "cheap"), ("05:30", "00:30", "day")],
    "cosy": [
        ("04:00", "07:00", "cheap"), ("13:00", "16:00", "cheap"),
        ("16:00", "19:00", "peak"), ("07:00", "13:00", "day"), ("19:00", "04:00", "day"),
    ],
    # Intelligent Octopus Go: the fixed cheap window only (smart-charge extras out of scope).
    "intelligent": [("23:30", "05:30", "cheap"), ("05:30", "23:30", "day")],
}

TARIFF_LABELS = {
    "flux": "Flux", "go": "Octopus Go", "cosy": "Cosy Octopus",
    "intelligent": "Intelligent Octopus Go", "agile": "Agile Octopus",
}

HALF_HOURS_PER_DAY = 48


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --------------------------------------------------------------------------- #
# Inverter drift check (phase 31)                                              #
# --------------------------------------------------------------------------- #

DRIFT_CHECK_INTERVAL_HOURS = 2

DRIFT_CHECK_AFTER_PUSH_MINUTES = 10


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Tomorrow's battery need runs from the end of the cheap window to the end of
# the peak (05:00-19:00): the part the overnight charge has to carry.
MULTI_DAY_NEED_HOURS = (PEAK_END_MINUTES - 5 * 60) / 60

# "Cost of 1 kWh now" sensor (phase 38).
MARGINAL_SOLAR_MIN_EXPORT_W = 100.0


def band_key_at(minutes: int) -> str:
    """The `kpi_prices_pence` import band key for a local minute of day."""
    if 2 * 60 <= minutes < TRIM_FROM_MINUTES:  # Flux off-peak 02:00-05:00
        return "offpeak"
    if PEAK_START_MINUTES <= minutes < PEAK_END_MINUTES:
        return "peak"
    return "day"


def price_at(bands: list[tuple[int, int, float]], minutes: int) -> float | None:
    """Price of the minute band (from `flux_bands_from_charges`) covering ``minutes``; None when none does."""
    for start, end, price in bands:
        inside = start <= minutes < end if start < end else (minutes >= start or minutes < end)
        if inside:
            return price
    return None


def solar_is_spare(grid_w: float | None, pv_w: float | None) -> bool:
    """Exporting and PV covers the export, so the next kWh used is lost solar export."""
    return (
        isinstance(grid_w, (int, float)) and isinstance(pv_w, (int, float))
        and grid_w < -MARGINAL_SOLAR_MIN_EXPORT_W and pv_w >= -grid_w
    )


def marginal_cost_pence(
    minutes: int,
    soc: float | None,
    reserve_soc: float | None,
    solar_spare: bool,
    prices: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    wear_pence: float | None = None,
    efficiency: float = ROUND_TRIP_EFFICIENCY,
) -> dict[str, Any]:
    """{"pence", "source"} for using 1 kWh at ``minutes``; pence None when its price is unknown (never 0p).

    Spare solar costs the export it displaces. Battery above ``reserve_soc``
    costs its off-peak replacement (/ round-trip efficiency + wear), or the
    current export price if higher (that kWh could have been sold). Otherwise
    the grid import price for the band. The off-peak band is always grid.
    """
    band = band_key_at(minutes)
    export = price_at(export_bands, minutes)
    if solar_spare:
        return {"pence": round_or_none(export), "source": "solar"}
    if band != "offpeak" and isinstance(soc, (int, float)) and isinstance(reserve_soc, (int, float)) and soc > reserve_soc:
        offpeak = prices.get("offpeak")
        if offpeak is None:
            return {"pence": None, "source": "battery"}
        cost = offpeak / efficiency + (wear_pence or 0.0)
        if export is not None:
            cost = max(cost, export)
        return {"pence": round(cost, 2), "source": "battery"}
    return {"pence": round_or_none(prices.get(band)), "source": "grid"}


# Value-of-the-system report (phase 37). Same 00:00-22:00 KPI day as day_kpis.
# (start, end, import band, can have solar)
_BASELINE_SEGMENTS = (
    ("00:00", "02:00", "day", False),
    ("02:00", "05:00", "offpeak", False),
    ("05:00", "16:00", "day", True),
    ("16:00", "19:00", "peak", True),
    ("19:00", "22:00", "day", True),
)


def _seg_value(snapshots: dict[str, dict[str, Any]], start: str, end: str, key: str) -> float | None:
    """Meter delta for a segment; the daily meters read 0 at midnight."""
    if start == "00:00":
        return _snap(snapshots, end, key)
    return _snap_delta(snapshots, start, end, key)


def _pence(kwh: float, price: float | None) -> float | None:
    """kWh × price; no energy costs 0 even without a price, otherwise a missing price is None."""
    if kwh <= 0:
        return 0.0
    return None if price is None else kwh * price


UPGRADE_HISTORY_DAYS = 90

CAPACITY_HISTORY_DAYS = 60


def record_for_date(record: dict[str, Any] | None, date: str) -> dict[str, Any]:
    """The stored record if it is for `date`, else {} (a stale day is never re-sent)."""
    return record if record and record.get("date") == date else {}


NOTIFICATION_KEEP_CHARS = 500

# --------------------------------------------------------------------------- #
# Phase 49: PV string health (MPPT0 vs MPPT1 share)                            #
# --------------------------------------------------------------------------- #

STRING_SAMPLE_FROM_HOUR = 10   # like-for-like hours: the string split drifts with the sun's angle

STRING_SAMPLE_TO_HOUR = 15

STRING_MIN_TOTAL_KW = 1.0      # below this a "share" is noise

STRING_ALERT_STREAK = 3        # consecutive anomalous days before a notification

# ---- Phase 84: inverter low-battery SOC floor (read-only review) ---------------------------------------
FLOOR_CANDIDATE_SOC = 15  # what-if floor for the £ estimate; the setting is never written

MPC_DAY_FROM_MINUTES = 6 * 60

FORECAST_QUANTILE_HISTORY_DAYS = 395  # the 13-month log

DYNAMIC_SHADOW_MINUTE = 22 * 60 + 5  # after tomorrow's Agile prices (~16:00) and the 22:00 snapshot

ELECTRIFY_HISTORY_DAYS = 395  # the 13-month log
