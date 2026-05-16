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
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DATA_DIR_NAME = "sunsynk_optimizer_data"


class DataLogger:
    """Appends JSONL records and analyses history for adaptive corrections."""

    _MIN_DAYS_FORECAST_CORRECTION = 7   # fewer paired days → too noisy to trust a ratio
    _MIN_DAYS_SOC_ADJUSTMENT = 5        # minimum for drain and evening-nudge corrections
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
        record: dict[str, Any] = {
            "type": "import_plan",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        for key in (
            "date",
            "soc",
            "raw_forecast_kwh",
            "forecast_correction_factor",
            "solar_forecast_kwh",
            "forecast_band",
            "target_soc",
            "target_soc_reason",
            "soc_adjustment",
            "overnight_drain_adjustment",
            "flux1_end",
            "logic_branch",
            "is_full_day",
            "selected_full_charge_day",
        ):
            if key in plan:
                record[key] = plan[key]
        await self._async_append(record)

    async def async_log_full_charge_scores(
        self, scores: dict[str, float], chosen_day: str
    ) -> None:
        """Log the weekly full-charge day selection and its weather scores."""
        await self._async_append(
            {
                "type": "full_charge_day",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "chosen_day": chosen_day,
                "scores": scores,
            }
        )

    async def async_log_morning_state(
        self,
        date: str,
        morning_soc: float,
        morning_pv_power: float,
    ) -> None:
        """Log SOC and PV power at 06:00 — just before solar typically starts."""
        await self._async_append(
            {
                "type": "morning_state",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "date": date,
                "morning_soc": round(morning_soc, 1),
                "morning_pv_power": round(morning_pv_power, 1),
            }
        )

    async def async_log_day_actuals(
        self,
        date: str,
        evening_soc: float,
        actual_solar_kwh: float,
        evening_export_disabled: bool,
    ) -> None:
        """Log end-of-day actuals captured at 22:00."""
        await self._async_append(
            {
                "type": "day_actuals",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "date": date,
                "evening_soc": round(evening_soc, 1),
                "actual_solar_kwh": round(actual_solar_kwh, 2),
                "evening_export_disabled": evening_export_disabled,
            }
        )

    # ------------------------------------------------------------------ #
    # History analysis                                                     #
    # ------------------------------------------------------------------ #

    async def async_load_paired_days(self, days: int = 30) -> list[dict[str, Any]]:
        """Return days where both an import_plan and day_actuals record exist."""
        records = await self.hass.async_add_executor_job(self._read_recent, days)
        return self._pair_records(records)

    def _read_recent(self, days: int) -> list[dict[str, Any]]:
        """Read all JSONL records from the relevant monthly files within the last `days` days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        months: set[str] = set()
        now = datetime.now(timezone.utc)
        for offset in range(days + 1):
            months.add((now - timedelta(days=offset)).strftime("%Y-%m"))

        records: list[dict[str, Any]] = []
        for month in months:
            path = os.path.join(self._data_dir, f"{month}.jsonl")
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            ts = rec.get("recorded_at", "")
                            if ts and datetime.fromisoformat(ts) >= cutoff:
                                records.append(rec)
                        except (json.JSONDecodeError, ValueError):
                            continue
            except OSError:
                continue
        return records

    def _pair_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Join import_plan + day_actuals + morning_state records by date into unified dicts."""
        plans = {
            r["date"]: r
            for r in records
            if r.get("type") == "import_plan" and "date" in r
        }
        actuals = {
            r["date"]: r
            for r in records
            if r.get("type") == "day_actuals" and "date" in r
        }
        mornings = {
            r["date"]: r
            for r in records
            if r.get("type") == "morning_state" and "date" in r
        }
        paired = []
        for date in set(plans) & set(actuals):
            plan = plans[date]
            actual = actuals[date]
            morning = mornings.get(date, {})
            morning_soc = morning.get("morning_soc")
            morning_pv_power = morning.get("morning_pv_power", 0.0)
            target_soc = plan.get("target_soc")
            overnight_drain_pct = (
                round(target_soc - morning_soc, 1)
                if morning_soc is not None and target_soc is not None
                else None
            )
            paired.append({
                "date": date,
                "solar_forecast_kwh": plan.get("solar_forecast_kwh", 0.0),
                "actual_solar_kwh": actual.get("actual_solar_kwh", 0.0),
                "forecast_band": plan.get("forecast_band"),
                "target_soc": target_soc,
                "morning_soc": morning_soc,
                "morning_pv_power": morning_pv_power,
                "overnight_drain_pct": overnight_drain_pct,
                "evening_soc": actual.get("evening_soc", 0.0),
                "evening_export_disabled": actual.get("evening_export_disabled", False),
                "is_full_day": plan.get("is_full_day", False),
            })
        return paired

    def compute_forecast_correction(self, paired_days: list[dict[str, Any]]) -> float:
        """Return mean(actual/forecast) ratio over recent days, capped at [0.5, 3.0].

        Returns 1.0 (no correction) until at least 7 paired days exist.
        Skips days where forecast was near-zero to avoid division noise.
        """
        valid = [d for d in paired_days if d["solar_forecast_kwh"] > 0.5]
        if len(valid) < self._MIN_DAYS_FORECAST_CORRECTION:
            return 1.0
        ratios = [d["actual_solar_kwh"] / d["solar_forecast_kwh"] for d in valid]
        factor = sum(ratios) / len(ratios)
        return max(0.5, min(3.0, round(factor, 3)))

    def compute_soc_target_adjustment(
        self, paired_days: list[dict[str, Any]], forecast_band: str
    ) -> int:
        """Return +5, -5, or 0 to nudge overnight target SOC based on evening outcomes.

        Uses only non-full-charge days where export wasn't forcibly disabled,
        since those are the days where the import plan target is the sole driver.
        Returns 0 until at least 5 matching days exist.
        """
        relevant = [
            d for d in paired_days
            if d["forecast_band"] == forecast_band
            and not d["is_full_day"]
            and not d["evening_export_disabled"]
            and d.get("actual_solar_kwh", 0) < self._HIGH_SOLAR_THRESHOLD_KWH
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
        self, paired_days: list[dict[str, Any]]
    ) -> int:
        """Return extra % to add to target_soc to compensate for overnight battery drain.

        Measures the SOC difference between what was charged to (target_soc) and
        what remained at 06:00 (morning_soc). Only uses days where PV power at 6am
        was negligible (<50W) so the reading isn't contaminated by early solar.
        Rounds to nearest 5% and caps at 20% to avoid overreacting to outliers.
        Returns 0 until at least 5 valid days exist.
        """
        valid = [
            d for d in paired_days
            if d.get("overnight_drain_pct") is not None
            and d["overnight_drain_pct"] >= 0
            and d.get("morning_pv_power", 0) < 50  # <50 W means solar hasn't started; reading is pure battery drain
            and not d["is_full_day"]
        ]
        if len(valid) < self._MIN_DAYS_SOC_ADJUSTMENT:
            return 0
        mean_drain = sum(d["overnight_drain_pct"] for d in valid) / len(valid)
        rounded = round(mean_drain / 5) * 5
        return max(0, min(20, int(rounded)))  # cap at 20% to avoid overreacting to outlier nights

    def count_forecast_correction_days(self, paired_days: list[dict[str, Any]]) -> int:
        """Return how many valid paired days exist toward the forecast correction threshold."""
        return len([d for d in paired_days if d["solar_forecast_kwh"] > 0.5])

    def count_drain_adjustment_days(self, paired_days: list[dict[str, Any]]) -> int:
        """Return how many valid morning-state days exist toward the drain adjustment threshold."""
        return len([
            d for d in paired_days
            if d.get("overnight_drain_pct") is not None
            and d["overnight_drain_pct"] >= 0
            and d.get("morning_pv_power", 0) < 50
            and not d["is_full_day"]
        ])

    def count_soc_adjustment_days(self, paired_days: list[dict[str, Any]], forecast_band: str) -> int:
        """Return how many relevant days exist toward the evening SOC nudge threshold."""
        return len([
            d for d in paired_days
            if d["forecast_band"] == forecast_band
            and not d["is_full_day"]
            and not d["evening_export_disabled"]
            and d.get("actual_solar_kwh", 0) < self._HIGH_SOLAR_THRESHOLD_KWH
        ])

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

    async def _async_append(self, record: dict[str, Any]) -> None:
        """Offload the blocking file write to the executor so it doesn't block the event loop."""
        await self.hass.async_add_executor_job(self._write_record, record)

    def _write_record(self, record: dict[str, Any]) -> None:
        """Append one JSON line to the current month's JSONL file, creating it if needed."""
        os.makedirs(self._data_dir, exist_ok=True)
        month_file = os.path.join(
            self._data_dir,
            f"{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl",
        )
        try:
            with open(month_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            _LOGGER.exception("Failed to write data log to %s", month_file)
