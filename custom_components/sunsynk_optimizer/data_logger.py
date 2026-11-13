# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Records forecast decisions and actuals for retrospective analysis."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from .const import INTEGRATION_VERSION
from .planning import (
    percentile,
    DAY_KPI_FIELDS,
    MEASURED_RATE_NIGHTS,
    charge_shortfall_suspect,
    drain_pct_to_0500,
    learned_measured_rate,
    measured_charge_kw,
    net_cost_gbp,
    round_or_none,
)

_LOGGER = logging.getLogger(__name__)

# Phase 91: record types written to their own monthly file ("<prefix>YYYY-MM.jsonl").
# mpc_plan is two-thirds of all lines and is never read back, so it stays out of the main log.
_SIDE_FILE_PREFIX = {"mpc_plan": "mpc-"}
_SKIP_LINE_MARKERS = tuple(f'"type": "{name}"' for name in _SIDE_FILE_PREFIX)  # older main-log lines
_CACHE_LOCK = threading.RLock()  # reads and writes run in executor threads

DATA_DIR_NAME = "sunsynk_optimizer_data"

# Every record type the logger writes (phase 100): the deploy notice lists the ones new since the last start.
RECORD_TYPES = (
    "battery_floor",
    "battery_health",
    "capacity_estimate",
    "charge_outcome",
    "charge_watchdog",
    "daily_cost",
    "day_actuals",
    "day_kpis",
    "dynamic_tariff",
    "flex_event",
    "free_event",
    "full_charge_day",
    "full_charge_recheck",
    "grid_notice",
    "half_hourly_export",
    "half_hourly_import",
    "hourly_profile",
    "import_causes",
    "import_plan",
    "inverter_drift",
    "inverter_event",
    "meter_reconciliation_export",
    "meter_reconciliation_import",
    "meter_snapshot",
    "morning_state",
    "mpc_plan",
    "multi_day_plan",
    "outage",
    "overnight_load",
    "peak_cover",
    "peak_export",
    "peak_export_recheck",
    "peak_window_usage",
    "pv_strings",
    "tou_shadow",
    "trim_shadow",
)

# import_plan fields persisted to the JSONL log. `away` and
# `effective_charge_rate_kw` are read back by _pair_records for the home/away
# calibration split and the last-known charge-rate fallback respectively.
_IMPORT_PLAN_FIELDS = (
    "date",
    "soc",
    "raw_forecast_kwh",
    "forecast_correction_factor",
    "forecast_correction_weighted",
    "forecast_correction_weighted_basis",
    "forecast_quantiles",
    "forecast_quantiles_cloud",
    "cloud_cover_pct",
    "outdoor_temp_c",
    "target_soc_newsvendor",
    "newsvendor_ratio",
    "newsvendor_quantile",
    "newsvendor_under_p",
    "newsvendor_over_p",
    "days_since_full",
    "full_charge_due",
    "full_charge_skip_would",
    "solar_forecast_kwh",
    "forecast_band",
    "target_soc",
    "target_soc_reason",
    "soc_adjustment",
    "full_window_end",
    "full_window_live",
    "overnight_drain_adjustment",
    "flux1_end",
    "logic_branch",
    "is_full_day",
    "selected_full_charge_day",
    "away",
    "effective_charge_rate_kw",
    "avg_consumption_kw",
    "load_source",
    "learned_load_kw",
    "charge_efficiency",
    "window_load_kwh",
    "grid_kwh_needed",
    "hours_to_solar",
    "import_feedback_adjustment",
    "import_feedback_reason",
    "import_feedback_live",
    "gentle_current_a",
    "gentle_charge_applied",
    "battery_temp_c",
    "measured_charge_rate_kw",
    "measured_charge_rate_live",
    "user_override",
)


def _record(record_type: str, **fields: Any) -> dict[str, Any]:
    """Build a log record with its type, a UTC recorded_at timestamp and the integration version."""
    return {
        "type": record_type,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "version": INTEGRATION_VERSION,
        **fields,
    }


DAILY_COST_FIELDS = ("actual_import_cost_gbp", "actual_export_income_gbp", "actual_gas_cost_gbp")


def merge_daily_cost_fields(existing: dict[str, Any], new: dict[str, float | None]) -> dict[str, float | None] | None:
    """Fill unset daily_cost fields of `existing` from `new`; None if that adds nothing.

    Already-logged values always win, so a later re-read can complete a
    partial day but never rewrite one.
    """
    merged = {f: existing.get(f) for f in DAILY_COST_FIELDS}
    added = False
    for field_name in DAILY_COST_FIELDS:
        value = new.get(field_name)
        if merged[field_name] is None and value is not None:
            merged[field_name] = round_or_none(value)
            added = True
    return merged if added else None


class DataLogger:
    """Appends JSONL records and analyses history for adaptive corrections."""

    _MIN_DAYS_FORECAST_CORRECTION = 7   # fewer paired days → too noisy to trust a ratio
    _MIN_DAYS_SOC_ADJUSTMENT = 5        # minimum for drain and evening-nudge corrections
    _DEFAULT_DRAIN_ADJUSTMENT = 15      # home fallback when fewer than 5 drain days exist — avoids cliff-edge drop to 0%
    _DEFAULT_DRAIN_ADJUSTMENT_AWAY = 8  # away fallback: holidays are known-low-drain, so start lower than home
    _DRAIN_EXCLUDE_SHORT = True         # phase 53: drop nights whose 05:00 gap is charge shortfall, not drain
    _DRAIN_USE_0500 = True              # phase 63: drain measured to 05:00 (bridge starts there), not 06:00
    _DRAIN_PERCENTILE = 75              # buffer sized to cover ~3 of 4 nights, not the average night
    _EVENING_SOC_LOW = 20.0             # below this at 22:00 → battery ran low; we under-charged
    _EVENING_SOC_HIGH = 35.0            # above this at 22:00 → battery still full; we over-charged
    _RETAIN_MONTHS = 13                 # one full year + one month so year-over-year patterns are always available
    _HIGH_SOLAR_THRESHOLD_KWH = 15.0   # days above this excluded from evening nudge — high SOC is solar-caused, not import-caused

    def __init__(self, hass: HomeAssistant) -> None:
        """Resolve the data directory path from the HA config directory."""
        self.hass = hass
        self._data_dir = hass.config.path(DATA_DIR_NAME)

    async def async_log_import_plan(self, plan: dict[str, Any]) -> None:
        """Log the overnight import plan decision made at 01:55."""
        await self._async_append(
            _record("import_plan", **{key: plan[key] for key in _IMPORT_PLAN_FIELDS if key in plan})
        )

    async def async_log_full_charge_scores(
        self, scores: dict[str, float], chosen_day: str
    ) -> None:
        """Log the weekly full-charge day selection and its weather scores."""
        await self._async_append(_record("full_charge_day", chosen_day=chosen_day, scores=scores))

    async def async_log_full_charge_recheck(self, **fields: Any) -> None:
        """Log the daily full-charge-day re-check (moved or not, with the reason)."""
        await self._async_append(_record("full_charge_recheck", **fields))

    async def async_log_morning_state(
        self,
        date: str,
        morning_soc: float | None,
        morning_pv_power: float,
        overnight_load_kwh: float | None = None,
    ) -> None:
        """Log SOC and PV power at 06:00 — just before solar typically starts.

        overnight_load_kwh is the SolarSynkV3 daily load total read at 06:00
        (i.e. 00:00-06:00 household consumption). It's a real energy figure
        alongside the SOC-derived overnight_drain_pct (computed elsewhere from
        target_soc - morning_soc), which is a useful cross-check since the
        SOC-based figure inherits the inverter's own SOC-estimation noise.
        """
        await self._async_append(
            _record(
                "morning_state",
                date=date,
                morning_soc=round_or_none(morning_soc, 1),
                morning_pv_power=round(morning_pv_power, 1),
                overnight_load_kwh=round_or_none(overnight_load_kwh),
            )
        )

    async def async_log_peak_window_usage(
        self,
        date: str,
        peak_load_kwh: float,
        peak_grid_import_kwh: float,
        peak_grid_export_kwh: float,
    ) -> None:
        """Log household load and grid import/export during the 16:00-19:00 window.

        Diagnostic only: nothing currently auto-tunes CONF_EXPORT_DISABLE_THRESHOLD
        from this data. It exists so the cost of the peak-rate window can be
        reviewed directly instead of inferred from point-in-time grid_pac samples.
        """
        await self._async_append(
            _record(
                "peak_window_usage",
                date=date,
                peak_load_kwh=round(peak_load_kwh, 2),
                peak_grid_import_kwh=round(peak_grid_import_kwh, 2),
                peak_grid_export_kwh=round(peak_grid_export_kwh, 2),
            )
        )

    async def async_merge_daily_cost(self, date: str, values: dict[str, float | None]) -> dict[str, Any] | None:
        """Merge settled Octopus cost fields into the daily_cost record for `date`.

        The import, export and gas sensors settle on independent schedules
        (gas meters commonly report a day behind electricity), so one day's
        record is assembled across several reads. Only fields still unset
        on the existing record are filled — a logged value is never
        overwritten. When something new was added, a complete superseding
        record is appended (_pair_records keeps the last record per date);
        returns that record, or None when nothing changed. net_cost_gbp is
        left to _pair_records so a partial day never carries a "complete"
        net figure in the raw log.
        """
        return await self.hass.async_add_executor_job(self._merge_daily_cost, date, values)

    def _merge_daily_cost(self, date: str, values: dict[str, float | None]) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc)
        try:
            start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        # A late catch-up can land in a later month's file than the date's own.
        existing: dict[str, Any] = {}
        for month in self._months_between(min(start, now), now):
            for _when, rec in self._load_file(os.path.join(self._data_dir, f"{month}.jsonl")):
                if rec.get("type") == "daily_cost" and rec.get("date") == date:
                    existing = rec
        merged = merge_daily_cost_fields(existing, values)
        if merged is None:
            return None
        record = _record("daily_cost", date=date, **merged)
        self._append_line(self._current_month_file(), record)
        return record

    async def async_log_day_actuals(
        self,
        date: str,
        evening_soc: float | None,
        actual_solar_kwh: float | None,
        evening_export_disabled: bool,
        day_load_kwh: float | None = None,
        day_grid_import_kwh: float | None = None,
        day_grid_export_kwh: float | None = None,
        clipped_solar_kwh: float | None = None,
        overnight_target_too_high: bool | None = None,
        export_derated_min: int | None = None,
    ) -> None:
        """Log end-of-day actuals captured at 22:00.

        day_load_kwh is the SolarSynkV3 daily household load total — logged
        directly so future consumption analysis doesn't have to infer load from
        the SOC swing (which conflates load with solar availability).
        """
        await self._async_append(
            _record(
                "day_actuals",
                date=date,
                evening_soc=round_or_none(evening_soc, 1),
                actual_solar_kwh=round_or_none(actual_solar_kwh),
                evening_export_disabled=evening_export_disabled,
                day_load_kwh=round_or_none(day_load_kwh),
                day_grid_import_kwh=round_or_none(day_grid_import_kwh),
                day_grid_export_kwh=round_or_none(day_grid_export_kwh),
                clipped_solar_kwh=round_or_none(clipped_solar_kwh),
                overnight_target_too_high=overnight_target_too_high,
                export_derated_min=export_derated_min,
            )
        )

    async def async_log_charge_watchdog(self, **fields: Any) -> None:
        """Log the nightly charge watchdog outcome (phase 22): one record per night."""
        await self._async_append(_record("charge_watchdog", **fields))

    async def async_log_charge_outcome(self, **fields: Any) -> None:
        """Log the overnight charge result (phase 54): one record per night."""
        await self._async_append(_record("charge_outcome", **fields))

    async def async_log_multi_day_plan(self, **fields: Any) -> None:
        """Log a multi-day low-solar shadow decision (phase 39): per stage, inside a low run only."""
        await self._async_append(_record("multi_day_plan", **fields))

    async def async_log_inverter_drift(self, **fields: Any) -> None:
        """Log an inverter drift episode (phase 31): one record per new drift signature."""
        await self._async_append(_record("inverter_drift", **fields))

    async def async_log_inverter_event(self, **fields: Any) -> None:
        """Log a newly seen inverter fault/event (phase 43): deduped by the caller on the event key."""
        await self._async_append(_record("inverter_event", **fields))

    async def async_log_grid_notice(self, **fields: Any) -> None:
        """Log a cloud grid lost/restored notice (phase 43 follow-up): deduped by the caller on notice id."""
        await self._async_append(_record("grid_notice", **fields))

    async def async_log_hourly_profile(self, **fields: Any) -> None:
        """Log the day's hourly soc / grid import / load profile (phase 86): one per date (deduped)."""
        await self._async_append(_record("hourly_profile", **fields))

    async def async_log_battery_floor(self, **fields: Any) -> None:
        """Log the inverter low-battery SOC review (phase 84): one per date (deduped)."""
        await self._async_append(_record("battery_floor", **fields))

    async def async_log_battery_health(self, **fields: Any) -> None:
        """Log the daily BMS state-of-health snapshot (phase 75): one per date (deduped)."""
        await self._async_append(_record("battery_health", **fields))

    async def async_log_meter_snapshot(self, **fields: Any) -> None:
        """Log a cumulative daily-meter snapshot (phase 14): one per date+time."""
        await self._async_append(_record("meter_snapshot", **fields))

    async def async_log_day_kpis(self, **fields: Any) -> None:
        """Log the 22:00 efficiency KPIs (phase 14): one per day."""
        await self._async_append(_record("day_kpis", **fields))

    async def async_log_import_causes(self, **fields: Any) -> None:
        """Log the day's grid import by cause (phase 42): one per day."""
        await self._async_append(_record("import_causes", **fields))

    async def async_log_outage(self, **fields: Any) -> None:
        """Log a closed grid outage (phase 48): start, end, SOC low point, battery kWh served. Not deduped."""
        await self._async_append(_record("outage", **fields))

    async def async_log_mpc_plan(self, **fields: Any) -> None:
        """Shadow MPC plan summary every 30 min (phase 69); never read back by learning."""
        await self._async_append(_record("mpc_plan", **fields))

    async def async_log_flex_event(self, **fields: Any) -> None:
        """What the battery could offer a paid export event (phase 77, shadow): one per event start."""
        await self._async_append(_record("flex_event", **fields))

    async def async_log_tou_shadow(self, **fields: Any) -> None:
        """Native TOU slots the plan would need vs the inverter's (phase 73): one per plan run."""
        await self._async_append(_record("tou_shadow", **fields))

    async def async_log_dynamic_tariff(self, **fields: Any) -> None:
        """Nightly Agile-vs-Flux shadow MPC comparison (phase 76): one per date."""
        await self._async_append(_record("dynamic_tariff", **fields))

    async def async_log_pv_strings(self, **fields: Any) -> None:
        """Log the day's per-string PV energy and MPPT0 share (phase 49): one per day."""
        await self._async_append(_record("pv_strings", **fields))

    async def async_log_peak_export(self, **fields: Any) -> None:
        """Log the 16:00 peak surplus export decision (phase 17): one per day."""
        await self._async_append(_record("peak_export", **fields))

    async def async_log_trim_shadow(self, **fields: Any) -> None:
        """Log what a deferred trim would have done (phase 65): one per trim, not deduped."""
        await self._async_append(_record("trim_shadow", **fields))

    async def async_log_peak_export_recheck(self, **fields: Any) -> None:
        """Log a 17:00/18:00 peak export re-check (phase 66): not deduped, two a day."""
        await self._async_append(_record("peak_export_recheck", **fields))

    async def async_log_peak_cover(self, **fields: Any) -> None:
        """Log the midday peak-cover decision (phase 30): one per day."""
        await self._async_append(_record("peak_cover", **fields))

    async def async_load_peak_cover(self, date: str) -> dict[str, Any]:
        """The logged peak_cover decision for ``date`` ({} if none), for a report after a restart."""
        records = await self.hass.async_add_executor_job(self._read_recent, 2)
        return latest_record(records, "peak_cover", date)

    async def async_log_overnight_load(self, **fields: Any) -> None:
        """Log the 00:00-05:00 house load (phase 26): one per day."""
        await self._async_append(_record("overnight_load", **fields))

    async def async_log_half_hourly(self, kind: str, date: str, kwh: list[float]) -> None:
        """Log one settled day's 48 half-hourly kWh (phase 28); ``kind`` is "import" or "export"."""
        await self._async_append(_record(f"half_hourly_{kind}", date=date, kwh=kwh))

    async def async_load_half_hourly(self, days: int = 75) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        """({date: 48 kWh} import, same for export) from recent half-hourly records."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        out: tuple[dict[str, list[float]], dict[str, list[float]]] = ({}, {})
        for rec in records:
            for target, kind in zip(out, ("import", "export"), strict=True):
                if rec.get("type") == f"half_hourly_{kind}" and rec.get("date") and isinstance(rec.get("kwh"), list):
                    target[str(rec["date"])] = rec["kwh"]
        return out

    async def async_log_meter_reconciliation(self, kind: str, date: str, **fields: Any) -> None:
        """Log one day's inverter-vs-smart-meter comparison (phase 44); ``kind`` is "import" or "export"."""
        await self._async_append(_record(f"meter_reconciliation_{kind}", date=date, **fields))

    async def async_load_reconciliation_inputs(
        self, days: int = 30
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """({date: day_actuals}, {date: import reconciliation}, {date: export reconciliation})."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        actuals: dict[str, dict[str, Any]] = {}
        recon: tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]] = ({}, {})
        for rec in records:
            date = rec.get("date")
            if not date:
                continue
            if rec.get("type") == "day_actuals":
                actuals[str(date)] = rec
            elif rec.get("type") == "meter_reconciliation_import":
                recon[0][str(date)] = rec
            elif rec.get("type") == "meter_reconciliation_export":
                recon[1][str(date)] = rec
        return actuals, recon[0], recon[1]

    async def async_recent_overnight_loads(self, today: str, days: int = 30) -> list[dict[str, Any]]:
        """Prior nights' {"load_kwh", "away"} from overnight_load records (excluding ``today``)."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        return [
            {"load_kwh": r.get("load_kwh"), "away": bool(r.get("away"))}
            for r in records
            if r.get("type") == "overnight_load" and r.get("date") != today
        ]

    async def async_recent_peak_loads(self, days: int = 14) -> list[float]:
        """House load (kWh) in recent 16:00-19:00 windows, from peak_window_usage records."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        return [
            float(r["peak_load_kwh"])
            for r in records
            if r.get("type") == "peak_window_usage" and isinstance(r.get("peak_load_kwh"), (int, float))
        ]

    async def async_recent_peak_load_days(self, days: int = 14) -> list[tuple[str, float]]:
        """(date, kWh) of recent 16:00-19:00 house load, for the weekday/weekend split (phase 60)."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        return [
            (str(r["date"]), float(r["peak_load_kwh"]))
            for r in records
            if r.get("type") == "peak_window_usage" and r.get("date")
            and isinstance(r.get("peak_load_kwh"), (int, float))
        ]

    async def async_log_capacity_estimate(self, **fields: Any) -> None:
        """Monthly measured battery capacity (phase 34)."""
        await self._async_append(_record("capacity_estimate", **fields))

    async def async_load_snapshot_days(self, days: int) -> dict[str, dict[str, dict[str, Any]]]:
        """{date: {"HH:MM": snapshot}} for every date with meter snapshots in the last ``days``."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        dates = {r.get("date") for r in records if r.get("type") == "meter_snapshot" and r.get("date")}
        by_date = snapshots_by_date(records)
        return {date: by_date.get(date, {}) for date in sorted(dates)}

    async def async_load_meter_snapshots(self, date: str) -> dict[str, dict[str, Any]]:
        """Return {"HH:MM": snapshot} for `date` (first record per time wins)."""
        records = await self.hass.async_add_executor_job(self._read_recent, 2)
        return snapshots_for_date(records, date)

    async def async_log_free_event(self, event: dict[str, Any]) -> None:
        """Log a free-electricity-event lifecycle point (scheduled / done / cancelled).

        Tagged only for now — not read back by any adaptive learning; filter it
        out of the overnight-drain/evening-nudge history later if it turns out
        to skew results (see phases/10-free-electricity-event.md).
        """
        await self._async_append(_record("free_event", **event))


    # ------------------------------------------------------------------ #
    # History analysis                                                     #
    # ------------------------------------------------------------------ #

    async def async_load_records(self, days: int) -> list[dict[str, Any]]:
        """All raw records of the last ``days`` days (phase 41 scorecard); read-only."""
        return await self.hass.async_add_executor_job(self._read_recent, days)

    async def async_load_paired_days(self, days: int = 30) -> list[dict[str, Any]]:
        """Return days where both an import_plan and day_actuals record exist."""
        return await self.hass.async_add_executor_job(self._load_paired_days, days)

    def _load_paired_days(self, days: int) -> list[dict[str, Any]]:
        """Read and pair in the executor: the join is too slow for the event loop on a long window."""
        return self._pair_records(self._read_recent(days))

    def _read_recent(self, days: int) -> list[dict[str, Any]]:
        """Read all JSONL records from the relevant monthly files within the last `days` days."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        records: list[dict[str, Any]] = []
        for month in self._months_between(cutoff, now):
            for when, rec in self._load_file(os.path.join(self._data_dir, f"{month}.jsonl")):
                if when is not None and when >= cutoff:
                    records.append(dict(rec))  # a copy: the cached record must never be mutated
        return records

    def _file_cache(self) -> dict[str, list[Any]]:
        """{path: [(mtime_ns, size), [(recorded_at, record)], {(type, date, time)}]} (phase 91)."""
        return self.__dict__.setdefault("_parsed_files", {})

    def _load_file(self, path: str) -> list[tuple[datetime | None, dict[str, Any]]]:
        """Parsed rows of one monthly file, re-read only when its size or mtime changed.

        Past months never change, so they are parsed once per run; the current
        month is kept in step by _append_line. A record's time is None when it
        has no usable (timezone-aware) recorded_at.
        """
        with _CACHE_LOCK:
            cache = self._file_cache()
            signature = _file_signature(path)
            if signature is None:
                cache.pop(path, None)
                return []
            hit = cache.get(path)
            if hit is None or hit[0] != signature:
                rows = [(_recorded_at(rec), rec) for rec in self._iter_jsonl(path)]
                hit = cache[path] = [signature, rows, {_dedup_key(rec) for _when, rec in rows}]
            return hit[1]

    @staticmethod
    def _months_between(start: datetime, end: datetime) -> list[str]:
        """'YYYY-MM' labels for every month from start to end inclusive."""
        months = []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            months.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return months

    @staticmethod
    def _iter_jsonl(path: str):
        """Yield each parseable JSON object in a JSONL file; missing/unreadable file yields nothing."""
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or any(marker in line for marker in _SKIP_LINE_MARKERS):
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        yield rec
        except OSError:
            return

    def _pair_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join import_plan + day_actuals + morning_state + daily_cost records by date into unified dicts."""
        by_type: dict[str, dict[str, dict[str, Any]]] = {
            "import_plan": {}, "day_actuals": {}, "morning_state": {}, "daily_cost": {},
            "day_kpis": {}, "peak_export": {},
        }
        for r in records:
            bucket = by_type.get(r.get("type"))
            if bucket is not None and "date" in r:
                bucket[r["date"]] = r
        plans = by_type["import_plan"]
        actuals = by_type["day_actuals"]
        mornings = by_type["morning_state"]
        costs = by_type["daily_cost"]
        kpis = by_type["day_kpis"]
        peak_exports = by_type["peak_export"]
        paired = []
        snaps_by_date = snapshots_by_date(records)  # one pass, not one scan per date
        for date in set(plans) & set(actuals):
            plan = plans[date]
            actual = actuals[date]
            morning = mornings.get(date, {})
            cost = costs.get(date, {})
            import_cost = cost.get("actual_import_cost_gbp")
            export_income = cost.get("actual_export_income_gbp")
            gas_cost = cost.get("actual_gas_cost_gbp")
            morning_soc = morning.get("morning_soc")
            morning_pv_power = morning.get("morning_pv_power", 0.0)
            target_soc = plan.get("target_soc")
            overnight_drain_pct = (
                round(target_soc - morning_soc, 1)
                if morning_soc is not None and target_soc is not None
                else None
            )
            snaps = snaps_by_date.get(date, {})
            soc_0500 = (snaps.get("05:00") or {}).get("soc")
            snap_02, snap_05 = snaps.get("02:00") or {}, snaps.get("05:00") or {}
            drain_0500 = drain_pct_to_0500(
                target_soc, soc_0500, overnight_drain_pct, plan.get("flux1_end")
            )
            paired.append({
                "date": date,
                "solar_forecast_kwh": plan.get("solar_forecast_kwh", 0.0),
                "raw_forecast_kwh": plan.get("raw_forecast_kwh"),
                "effective_charge_rate_kw": plan.get("effective_charge_rate_kw"),
                "away": bool(plan.get("away", False)),
                "actual_solar_kwh": actual.get("actual_solar_kwh"),
                "forecast_band": plan.get("forecast_band"),
                "full_window": bool(plan.get("full_window_live", False)),
                "battery_temp_c": plan.get("battery_temp_c"),
                "measured_charge_kw": measured_charge_kw(
                    _meter_delta(snap_02, snap_05, "grid_import_kwh"),
                    _meter_delta(snap_02, snap_05, "load_kwh"),
                    plan.get("flux1_end"), plan.get("soc"), target_soc,
                    bool(snap_02.get("soc_from_cache")),
                ),
                "target_soc": target_soc,
                "morning_soc": morning_soc,
                "morning_pv_power": morning_pv_power,
                "overnight_load_kwh": morning.get("overnight_load_kwh"),
                "overnight_drain_pct": overnight_drain_pct,
                "overnight_drain_pct_0500": drain_0500,
                "drain_source": (
                    "soc_0500" if soc_0500 is not None and target_soc is not None
                    else ("scaled_0600" if overnight_drain_pct is not None else None)
                ),
                "charge_short": (
                    charge_shortfall_suspect(drain_0500, plan.get("flux1_end"))
                    if soc_0500 is not None else None
                ),
                "evening_soc": actual.get("evening_soc"),
                "evening_export_disabled": actual.get("evening_export_disabled", False),
                "day_load_kwh": actual.get("day_load_kwh"),
                "day_grid_import_kwh": actual.get("day_grid_import_kwh"),
                "day_grid_export_kwh": actual.get("day_grid_export_kwh"),
                "clipped_solar_kwh": actual.get("clipped_solar_kwh"),
                "overnight_target_too_high": actual.get("overnight_target_too_high"),
                "actual_import_cost_gbp": import_cost,
                "actual_export_income_gbp": export_income,
                "actual_gas_cost_gbp": gas_cost,
                "net_cost_gbp": net_cost_gbp(import_cost, export_income, gas_cost),
                "is_full_day": plan.get("is_full_day", False),
                "initial_soc": plan.get("soc"),
                "flux1_end": plan.get("flux1_end", ""),
                # Phase 87: read back by the cloud bins (82) and the drain/nudge filters (46).
                "cloud_cover_pct": plan.get("cloud_cover_pct"),
                "user_override": plan.get("user_override"),
                **{key: kpis.get(date, {}).get(key) for key in DAY_KPI_FIELDS},
                "kpi_version": kpis.get(date, {}).get("kpi_version"),
                # Phase 17: shadow or live, only days whose 16:00 decision was export.
                **_peak_export_fields(peak_exports.get(date, {})),
            })
        return paired

    def compute_forecast_correction(self, paired_days: list[dict[str, Any]]) -> float:
        """Return median(actual/forecast) ratio over recent days, capped at [0.5, 3.0].

        The denominator is the stored (already-corrected) forecast, which makes
        the update self-damping: at equilibrium the factor settles at
        sqrt(actual/raw_forecast) rather than the full raw bias. That
        under-corrects — deliberately the safe direction, since a forecast the
        planner believes is lower than reality means more overnight charge, not
        less. Median (not mean) so a single anomalous day (tiny forecast, big
        actual → unbounded ratio) cannot move the factor.

        Returns 1.0 (no correction) until at least 7 paired days exist.
        Skips days where forecast was near-zero to avoid division noise.
        """
        valid = [d for d in paired_days if self._is_forecast_sample(d)]
        if len(valid) < self._MIN_DAYS_FORECAST_CORRECTION:
            return 1.0
        ratios = sorted(d["actual_solar_kwh"] / d["solar_forecast_kwh"] for d in valid)
        factor = self._percentile(ratios, 50)
        return max(0.5, min(3.0, round(factor, 3)))

    def compute_soc_target_adjustment(
        self, paired_days: list[dict[str, Any]], forecast_band: str, away: bool = False
    ) -> int:
        """Return +5, -5, or 0 to nudge overnight target SOC based on evening outcomes.

        Uses only non-full-charge days where export wasn't forcibly disabled,
        since those are the days where the import plan target is the sole driver.
        Filtered to the current occupancy regime (home vs away) so a low-load
        holiday can't drag the home nudge, and vice versa. Returns 0 until at
        least 5 matching days exist.
        """
        relevant = [
            d for d in paired_days
            if d["forecast_band"] == forecast_band
            and not d["is_full_day"]
            and not d.get("full_window")  # phase 62: no gap after the charge, so no drain to measure
            and not d.get("user_override")  # phase 46
            and not d["evening_export_disabled"]
            and d.get("evening_soc") is not None
            and d.get("actual_solar_kwh") is not None
            and d["actual_solar_kwh"] < self._HIGH_SOLAR_THRESHOLD_KWH
            and bool(d.get("away", False)) == away
        ]
        if len(relevant) < self._MIN_DAYS_SOC_ADJUSTMENT:
            return 0
        mean_soc = sum(d["evening_soc"] for d in relevant) / len(relevant)
        if mean_soc > self._EVENING_SOC_HIGH:
            return -5  # battery still too full at 22:00 → over-charged overnight
        if mean_soc < self._EVENING_SOC_LOW:
            return 5   # battery too empty at 22:00 → under-charged overnight
        return 0

    def compute_overnight_drain_adjustment(
        self, paired_days: list[dict[str, Any]], away: bool = False
    ) -> int:
        """Return extra % to add to target_soc to compensate for overnight battery drain.

        Measures the SOC difference between what was charged to (target_soc) and
        what remained at 06:00 (morning_soc). Only uses days where PV power at 6am
        was negligible (<200W) so the reading isn't contaminated by early solar.

        Uses the 75th percentile of recent drains rather than the mean: this is a
        safety buffer, so it should cover the heavier nights (e.g. dishwasher /
        washing machine running overnight) rather than the typical night. The mean
        washes those out and leaves the battery short on appliance nights. The p75
        covers roughly 3 of every 4 nights while quiet weeks still pull it down.

        Only nights where a real overnight charge happened (initial SOC below
        target) are counted. On a "no-charge" night the battery started above
        target and simply discharged from a high point — target − morning then
        conflates the evening discharge with post-charge drain and inflates the
        buffer. Excluding those makes the metric correct by construction (it
        matched the summer result already, but protects winter nights when high
        targets make no-charge starts common).

        Rounds to nearest 5% and caps at 20% to avoid overreacting to outliers.
        Until at least 5 valid days exist it returns the regime's default
        (lower for away, since holidays are known-low-drain) so target SOC never
        drops to the bare bridge base when history is thin.
        """
        valid = [d for d in paired_days if self._is_drain_night(d, away)]
        if len(valid) < self._MIN_DAYS_SOC_ADJUSTMENT:
            return self._DEFAULT_DRAIN_ADJUSTMENT_AWAY if away else self._DEFAULT_DRAIN_ADJUSTMENT
        if self._DRAIN_EXCLUDE_SHORT:
            kept = [d for d in valid if not d.get("charge_short")]
            if len(kept) >= self._MIN_DAYS_SOC_ADJUSTMENT:
                valid = kept
        key = "overnight_drain_pct_0500" if self._DRAIN_USE_0500 else "overnight_drain_pct"
        drains = [d[key] if d.get(key) is not None else d["overnight_drain_pct"] for d in valid]
        buffer_drain = self._percentile(drains, self._DRAIN_PERCENTILE)
        rounded = round(buffer_drain / 5) * 5
        return max(0, min(20, int(rounded)))  # cap at 20% to avoid overreacting to outlier nights

    @staticmethod
    def _is_drain_night(d: dict[str, Any], away: bool = False) -> bool:
        """True if a paired day is a valid post-charge overnight-drain sample.

        Requires: a computed drain, negligible PV at 06:00 (so early solar isn't
        masking the drain), a non-full-charge day, that a real overnight charge
        happened (initial SOC below target), and that the day matches the given
        occupancy regime (home vs away) — so holiday nights are learned into the
        away profile only. A no-charge night (initial >= target) discharges from
        a high start and is not a post-charge drain measurement.
        """
        initial = d.get("initial_soc")
        target = d.get("target_soc")
        return (
            d.get("overnight_drain_pct") is not None
            and d["overnight_drain_pct"] >= 0
            and d.get("morning_pv_power", 0) < 200  # <200 W means net battery drain still dominates at 06:00
            and not d["is_full_day"]
            and not d.get("user_override")  # phase 46: a manual target is not a model-chosen one
            and initial is not None
            and target is not None
            and initial < target
            and bool(d.get("away", False)) == away
        )

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        """Percentile (0-100) via the shared helper; an empty list is 0.0."""
        return float(percentile(values, pct, default=0.0))

    def count_forecast_correction_days(self, paired_days: list[dict[str, Any]]) -> int:
        """Return how many valid paired days exist toward the forecast correction threshold."""
        return len([d for d in paired_days if self._is_forecast_sample(d)])

    @staticmethod
    def _is_forecast_sample(d: dict[str, Any]) -> bool:
        """A day usable for the forecast correction: a real forecast and a logged actual."""
        return d["solar_forecast_kwh"] > 0.5 and d.get("actual_solar_kwh") is not None

    def count_drain_adjustment_days(self, paired_days: list[dict[str, Any]], away: bool = False) -> int:
        """Return how many valid drain-night samples exist toward the drain threshold.

        Uses the same _is_drain_night predicate as compute_overnight_drain_adjustment
        (including the home/away regime) so the counter reflects the exact qualifying set.
        """
        return len([d for d in paired_days if self._is_drain_night(d, away)])

    def count_soc_adjustment_days(self, paired_days: list[dict[str, Any]], forecast_band: str, away: bool = False) -> int:
        """Return how many in-band non-full-charge days exist toward the evening SOC nudge threshold.

        Intentionally does NOT apply the high-solar exclusion so the counter reflects real
        progress during summer. The nudge computation (compute_soc_target_adjustment) still
        filters out high-solar days to avoid a false "over-charged" signal; when all in-band
        days are high-solar the nudge correctly returns 0 ("no adjustment needed"). Filtered
        to the current occupancy regime so it tracks the active (home vs away) profile.
        """
        return len([
            d for d in paired_days
            if d["forecast_band"] == forecast_band
            and not d["is_full_day"]
            and not d["evening_export_disabled"]
            and d.get("evening_soc") is not None
            and bool(d.get("away", False)) == away
        ])

    @staticmethod
    def _flux1_end_hours(flux1_end: str) -> float:
        """Convert 'HH:MM' to fractional hours, e.g. '03:15' → 3.25."""
        try:
            h, m = flux1_end.split(":")
            return int(h) + int(m) / 60.0
        except (ValueError, AttributeError):
            return 0.0

    def compute_effective_charge_rate_kw(
        self,
        paired_days: list[dict[str, Any]],
        battery_kwh: float,
        overnight_drain_adj: float,
    ) -> float | None:
        """Estimate actual battery charge rate from historical charging days.

        Compares estimated SOC at charge-end (back-calculated from morning SOC
        and drain rate) against initial SOC to derive how fast the battery charged.
        Returns None until 3+ valid calibration days exist. The charge rate is a
        physical property of the battery, so it is learned from HOME days only:
        away days back-calculate through an atypically low drain and would bias
        it. When away, the caller reuses this home-learned rate.
        """
        no_charge = [
            d for d in paired_days
            if d.get("initial_soc") is not None
            and d.get("target_soc") is not None
            and d["initial_soc"] >= d["target_soc"] - 5
            and d.get("morning_soc") is not None
            and d.get("morning_pv_power", 0) < 200
            and not d["is_full_day"]
            and not d.get("away", False)
        ]
        if no_charge:
            drain_rate = max(
                0.0,
                sum((d["initial_soc"] - d["morning_soc"]) / 3.75 for d in no_charge) / len(no_charge),
            )
        else:
            drain_rate = overnight_drain_adj / 4.0

        rates = []
        for d in paired_days:
            end_h = self._charge_calibration_end_hours(d)
            if end_h is None:
                continue
            charge_h = end_h - 2.0
            est_end_soc = min(d["morning_soc"] + drain_rate * (6.0 - end_h), float(d["target_soc"]))
            gained = max(0.0, est_end_soc - d["initial_soc"])
            if gained < 5:
                continue
            rates.append((gained / 100.0 * battery_kwh) / charge_h)

        if len(rates) < 3:
            return None
        return round(sum(rates) / len(rates), 2)

    def compute_measured_charge_rate_kw(
        self, paired_days: list[dict[str, Any]], battery_temp_c: float
    ) -> tuple[float, bool] | None:
        """Phase 61: rate measured from the meter snapshots, over the last 14 qualifying nights.

        Returns (kw, at_tonights_temperature) or None below 3 samples; the old
        SOC back-calculation stays the fallback. Full-window nights are skipped.
        """
        nights = sorted(
            (d for d in paired_days
             if d.get("measured_charge_kw") is not None
             and d.get("battery_temp_c") is not None
             and not d.get("full_window")),
            key=lambda d: d.get("date", ""),
        )[-MEASURED_RATE_NIGHTS:]
        return learned_measured_rate(
            [(float(d["measured_charge_kw"]), float(d["battery_temp_c"])) for d in nights],
            battery_temp_c,
        )

    @staticmethod
    def last_known_charge_rate_kw(paired_days: list[dict[str, Any]]) -> float | None:
        """Return the most recent non-null learned charge rate from history, or None.

        Used to seed the fallback so a fresh install/upgrade doesn't revert to the
        optimistic nameplate config for the days it takes calibration to recover.
        """
        for d in sorted(paired_days, key=lambda x: x.get("date", ""), reverse=True):
            rate = d.get("effective_charge_rate_kw")
            if rate is not None:
                return float(rate)
        return None

    def count_charge_rate_calibration_days(self, paired_days: list[dict[str, Any]]) -> int:
        """Return how many valid charging days exist toward charge rate calibration (needs 3)."""
        return len([d for d in paired_days if self._charge_calibration_end_hours(d) is not None])

    def _charge_calibration_end_hours(self, d: dict[str, Any]) -> float | None:
        """Flux 1 end (hours) of a night usable to calibrate the charge rate, else None.

        One predicate for compute_effective_charge_rate_kw and its progress counter:
        a home, non-full-charge night that charged 10%+ for 15 min+ and ended by 06:00.
        """
        initial, target, morning = d.get("initial_soc"), d.get("target_soc"), d.get("morning_soc")
        flux1 = d.get("flux1_end", "")
        if (
            initial is None or target is None or morning is None
            or initial <= 0 or not flux1
            or target - initial < 10
            or d.get("morning_pv_power", 0) >= 200
            or d.get("is_full_day")
            or d.get("away", False)
        ):
            return None
        end_h = self._flux1_end_hours(flux1)
        return end_h if end_h - 2.0 >= 0.25 and end_h <= 6.0 else None

    # ------------------------------------------------------------------ #
    # Retention                                                            #
    # ------------------------------------------------------------------ #

    async def async_prune_old_files(self, retain_months: int = _RETAIN_MONTHS) -> None:
        """Delete JSONL files older than retain_months. Runs in executor."""
        await self.hass.async_add_executor_job(self._prune_old_files, retain_months)

    def _prune_old_files(self, retain_months: int) -> None:
        """Delete JSONL files whose month falls outside the retention window."""
        if not os.path.isdir(self._data_dir):
            return
        now = datetime.now(timezone.utc)
        cutoff_ordinal = (now.year * 12 + now.month - 1) - retain_months
        try:
            entries = os.listdir(self._data_dir)
        except OSError:
            _LOGGER.exception("Could not list data directory %s", self._data_dir)
            return
        for filename in entries:
            if not filename.endswith(".jsonl"):
                continue
            stem = filename[:-6]
            for prefix in _SIDE_FILE_PREFIX.values():
                stem = stem.removeprefix(prefix)
            try:
                dt = datetime.strptime(stem, "%Y-%m")
            except ValueError:
                continue
            if (dt.year * 12 + dt.month - 1) <= cutoff_ordinal:
                path = os.path.join(self._data_dir, filename)
                try:
                    os.remove(path)
                    _LOGGER.info("Pruned old data log: %s", filename)
                except OSError:
                    _LOGGER.exception("Failed to delete old data log: %s", path)

    # ------------------------------------------------------------------ #
    # Write helpers                                                        #
    # ------------------------------------------------------------------ #

    # daily_cost is not deduped here — it merges via async_merge_daily_cost.
    _DEDUP_TYPES = (
        "import_plan", "morning_state", "day_actuals", "peak_window_usage", "charge_watchdog", "charge_outcome",
        "meter_snapshot", "day_kpis", "peak_export", "peak_cover", "overnight_load", "import_causes",
        "half_hourly_import", "half_hourly_export", "capacity_estimate", "pv_strings",
        "meter_reconciliation_import", "meter_reconciliation_export", "battery_health", "dynamic_tariff", "tou_shadow", "flex_event", "battery_floor", "hourly_profile",
    )

    async def _async_append(self, record: dict[str, Any]) -> None:
        """Offload the blocking file write to the executor so it doesn't block the event loop."""
        await self.hass.async_add_executor_job(self._write_record, record)

    def _write_record(self, record: dict[str, Any]) -> None:
        """Append one JSON line to the current month's JSONL file, creating it if needed.

        Per-day record types are skipped when a record with the same type, date
        (and time) is already logged, so an HA restart that crosses a scheduled
        capture (restart at 05:55, second morning_state at 06:00) cannot corrupt
        the paired days. The month of the record's own date is checked too, so
        the check still holds in the first days after a month roll.
        """
        with _CACHE_LOCK:
            month_file = self._current_month_file(_SIDE_FILE_PREFIX.get(record.get("type"), ""))
            if record.get("type") in self._DEDUP_TYPES and record.get("date"):
                seen_in = next((path for path in self._dedup_files(month_file, record) if self._record_exists(path, record)), None)
                if seen_in:
                    _LOGGER.info(
                        "Skipping duplicate %s record for %s (already in %s)",
                        record["type"], record["date"], os.path.basename(seen_in),
                    )
                    return
            self._append_line(month_file, record)

    def _current_month_file(self, prefix: str = "") -> str:
        return os.path.join(self._data_dir, f"{prefix}{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl")

    def _dedup_files(self, month_file: str, record: dict[str, Any]) -> list[str]:
        """The current month's file plus the file for the month of the record's date, when different."""
        files = [month_file]
        month = str(record["date"])[:7]
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            return files
        own = os.path.join(self._data_dir, f"{month}.jsonl")
        return files if own == month_file else files + [own]

    def _append_line(self, path: str, record: dict[str, Any]) -> None:
        with _CACHE_LOCK:
            cache = self._file_cache()
            hit = cache.get(path)
            in_step = hit is not None and hit[0] == _file_signature(path)
            try:
                os.makedirs(self._data_dir, exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except OSError:
                _LOGGER.exception("Failed to write data log to %s", path)
                cache.pop(path, None)
                return
            if in_step:  # keep the parsed copy in step instead of re-reading the file
                hit[1].append((_recorded_at(record), record))
                hit[2].add(_dedup_key(record))
                hit[0] = _file_signature(path)
            else:
                cache.pop(path, None)

    def _record_exists(self, path: str, record: dict[str, Any]) -> bool:
        """True if a record with this type and date (and time, for meter snapshots) is already in the file."""
        self._load_file(path)
        hit = self._file_cache().get(path)
        return hit is not None and _dedup_key(record) in hit[2]


def _file_signature(path: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _recorded_at(record: dict[str, Any]) -> datetime | None:
    """The record's timezone-aware recorded_at, or None (such records never match a read window)."""
    try:
        when = datetime.fromisoformat(record.get("recorded_at") or "")
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


def _dedup_key(record: dict[str, Any]) -> tuple[Any, Any, Any]:
    key = (record.get("type"), record.get("date"), record.get("time"))
    try:
        hash(key)
    except TypeError:
        return (None, None, None)
    return key


def _meter_delta(snap_a: dict[str, Any], snap_b: dict[str, Any], key: str) -> float | None:
    """snap_b[key] - snap_a[key], or None when either is missing or the meter reset (negative)."""
    a, b = snap_a.get(key), snap_b.get(key)
    if a is None or b is None or b < a:
        return None
    return b - a


def snapshots_for_date(records: list[dict[str, Any]], date: str) -> dict[str, dict[str, Any]]:
    """{"HH:MM": meter_snapshot} for one date; the first record per time wins (dedup)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.get("type") == "meter_snapshot" and rec.get("date") == date and rec.get("time"):
            out.setdefault(rec["time"], rec)
    return out


def snapshots_by_date(records: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """{date: {"HH:MM": meter_snapshot}} in one pass; the first record per time wins, as snapshots_for_date."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for rec in records:
        if rec.get("type") == "meter_snapshot" and rec.get("date") and rec.get("time"):
            out.setdefault(rec["date"], {}).setdefault(rec["time"], rec)
    return out


def latest_record(records: list[dict[str, Any]], record_type: str, date: str) -> dict[str, Any]:
    """The last ``record_type`` record for ``date`` without its recorded_at, or {}."""
    found = [r for r in records if r.get("type") == record_type and r.get("date") == date]
    return {k: v for k, v in found[-1].items() if k != "recorded_at"} if found else {}


def _peak_export_fields(rec: dict[str, Any]) -> dict[str, Any]:
    exporting = rec.get("decision") == "export"
    return {
        "peak_export_kwh": rec.get("export_kwh") if exporting else None,
        "peak_export_gain_gbp": rec.get("gain_gbp") if exporting else None,
        "peak_export_live": rec.get("live") if exporting else None,
    }
