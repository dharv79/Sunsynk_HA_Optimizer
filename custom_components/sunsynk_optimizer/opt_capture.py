# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: 06:00/22:00 captures, meter snapshots, KPIs, daily cost, hourly profile."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from homeassistant.util import dt as dt_util
from .const import (
    INTEGRATION_VERSION,
    CONF_AVG_CONSUMPTION_KW,
    CONF_WEEKEND_AVG_CONSUMPTION_KW,
    CONF_AUTO_AWAY_CALENDAR,
    CONF_AUTO_AWAY_KEYWORD,
    CONF_AUTO_AWAY_MIN_HOURS,
    CONF_AUTO_AWAY_PERSONS,
    CONF_AWAY_AVG_CONSUMPTION_KW,
    CONF_CHARGES,
    CONF_DATA_REPORT_TARGET,
    CONF_LOAD_PROFILE_LIVE,
    CONF_OCTOPUS_IMPORT_COST_SENSOR,
    CONF_OCTOPUS_EXPORT_INCOME_SENSOR,
    CONF_OCTOPUS_GAS_COST_SENSOR,
    CONF_OCTOPUS_IMPORT_CONSUMPTION_SENSOR,
    CONF_OCTOPUS_EXPORT_CONSUMPTION_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_AVG_CONSUMPTION_KW,
    DEFAULT_WEEKEND_AVG_CONSUMPTION_KW,
    DEFAULT_AUTO_AWAY_KEYWORD,
    DEFAULT_AUTO_AWAY_MIN_HOURS,
    DEFAULT_AWAY_AVG_CONSUMPTION_KW,
    DEFAULT_LOAD_PROFILE_LIVE,
)
from .data_logger import DAILY_COST_FIELDS
from .flux_helpers import (
    TARIFF_BANDS,
    price_source,
)
from .planning import (
    flux_bands_from_charges,
    BATTERY_LOW_SOC,
    parse_half_hourly_charges,
    meter_reconciliation,
    divergence_streak,
    auto_away,
    overnight_target_too_high,
    hourly_profile,
    reserve_hold_cost,
    no_battery_baseline,
    capacity_estimate_pending,
    record_for_date,
    recent_soc,
    daily_report_plans,
    day_kpis,
    soc_cap_shadow,
    evening_reserve_soc,
    latest_complete_cost_day,
    net_cost_gbp,
    event_report,
    free_event_kpi_credit,
    round_or_none,
    sum_field,
    IMPORT_CAUSES,
)
from .opt_common import (
    _OCTOPUS_CATCH_UP_DAYS,
    _octopus_oldest_date,
)

class CaptureMixin:
    """06:00/22:00 captures, meter snapshots, KPIs, daily cost, hourly profile (methods of SunsynkOptimizer)."""

    def _regime_load_kw(self, cfg: dict[str, Any], away: bool, is_weekend: bool = False) -> float:
        """Configured overnight load for the occupancy regime: away beats weekend beats weekday."""
        if away:
            return float(cfg.get(CONF_AWAY_AVG_CONSUMPTION_KW, DEFAULT_AWAY_AVG_CONSUMPTION_KW))
        if is_weekend:
            return float(cfg.get(CONF_WEEKEND_AVG_CONSUMPTION_KW, DEFAULT_WEEKEND_AVG_CONSUMPTION_KW))
        return float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))

    def _plan_load_kw(self, cfg: dict[str, Any]) -> float:
        """Tonight's plan load rate (phase 12 learned or config), else the config weekday rate."""
        load_kw = (self.coordinator.state.last_import_plan or {}).get("avg_consumption_kw")
        return float(load_kw if load_kw is not None else cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))

    def _read_octopus_previous_day_cost(self) -> dict[str, dict[str, float]]:
        """Read the optional Octopus Energy "previous accumulative cost" sensors.

        Returns {settled_date_iso: {daily_cost field: value}}, with each sensor
        filed under the date it actually reports. Blank config, unavailable or
        non-numeric sensors are omitted — mirrors the graceful-degrade shape of
        _get_hourly_forecast_kwh: never raises.

        These sensors reflect a PRIOR calendar day, and each settles on its own
        schedule: electricity may land a few hours after midnight or not until
        ~22:00 the next evening (hence reads at both 06:00 and 22:00), gas
        meters commonly report a day behind electricity, and Octopus billing
        can lag several days (seen around a UK bank holiday) while the HA
        integration keeps refreshing. Each sensor's own `last_reset` (start of
        the period it reports) therefore dates its reading, accepted within the
        last `_OCTOPUS_CATCH_UP_DAYS` days; a sensor without `last_reset` is
        assumed to be yesterday, and one outside the window is skipped so a
        value is never logged under the wrong date. The caller merges each
        date's fields into that day's record as they arrive.
        """
        yesterday = (dt_util.now() - timedelta(days=1)).date()
        oldest_allowed = yesterday - timedelta(days=_OCTOPUS_CATCH_UP_DAYS - 1)
        cfg = self.cfg
        by_date: dict[str, dict[str, float]] = {}

        for conf_key, field_name in (
            (CONF_OCTOPUS_IMPORT_COST_SENSOR, "actual_import_cost_gbp"),
            (CONF_OCTOPUS_EXPORT_INCOME_SENSOR, "actual_export_income_gbp"),
            (CONF_OCTOPUS_GAS_COST_SENSOR, "actual_gas_cost_gbp"),
        ):
            sensor_id = self._cfg_str(conf_key, cfg)
            state = self._available_state(sensor_id) if sensor_id else None
            if state is None:
                continue
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                continue
            raw = state.attributes.get("last_reset")
            last_reset = dt_util.parse_datetime(str(raw)) if raw else None
            reported = dt_util.as_local(last_reset).date() if last_reset is not None else yesterday
            if not oldest_allowed <= reported <= yesterday:
                continue
            by_date.setdefault(reported.isoformat(), {})[field_name] = value
        return by_date

    def _sample_hourly(self) -> None:
        """Add a timed sample of SOC and the daily import/load meters to today's buffer (phase 86)."""
        now = dt_util.now()
        today = now.date().isoformat()
        if self._hourly.get("date") != today:
            self._hourly = {"date": today, "samples": []}
        if len(self._hourly["samples"]) < 120:
            self._hourly["samples"].append({
                "minute": now.hour * 60 + now.minute, "soc": self._essential_state(self.battery_soc_entity),
                "grid_import_kwh": self._essential_state(self.day_grid_import_entity),
                "load_kwh": self._essential_state(self.day_load_entity),
            })

    async def _async_log_hourly_profile(self, date: str) -> dict[str, Any]:
        """22:00: log the hourly profile and the reserve-hold split (phase 86); shadow, no writes."""
        self._sample_hourly()
        samples = self._hourly["samples"] if self._hourly.get("date") == date else []
        hourly = hourly_profile(samples)
        if not hourly:
            return {}
        prices, _sources = self._tariff_prices(dt_util.parse_date(date))
        split = reserve_hold_cost(hourly, BATTERY_LOW_SOC, prices)
        await self.data_logger.async_log_hourly_profile(date=date, hourly=hourly, **split)
        return {"type": "hourly_profile", "date": date, "hourly": hourly, **split}

    async def _async_meter_snapshot(self, now) -> None:
        time = dt_util.as_local(now).strftime("%H:%M")
        await self._guarded(lambda: self.async_capture_meter_snapshot(time), "Meter snapshot")
        if time == "05:00":
            await self._guarded(self._async_overnight_load_check, "Overnight baseload check")
            await self._guarded(self.async_run_charge_outcome, "Charge outcome")
        # Until a first estimate exists, re-measure after each sample-closing snapshot.
        if time == "05:00" and capacity_estimate_pending(self.coordinator.state.battery_capacity_estimate):
            await self._guarded(lambda: self.async_run_capacity_estimate(notify=False), "Battery capacity estimate")

    async def _async_auto_away(self) -> None:
        """Set away mode from person/calendar signals (phase 27); no config = no-op."""
        cfg = self.cfg
        persons = [p.strip() for p in self._cfg_str(CONF_AUTO_AWAY_PERSONS).split(",") if p.strip()]
        calendar = self._cfg_str(CONF_AUTO_AWAY_CALENDAR)
        if not persons and not calendar:
            return
        now = dt_util.utcnow()
        homes: list[bool | None] = []
        hours: list[float | None] = []
        for entity in persons:
            st = self.hass.states.get(entity)
            if st is None or st.state in ("unknown", "unavailable", ""):
                homes.append(None)
                hours.append(None)
                continue
            homes.append(st.state == "home")
            hours.append((now - st.last_changed).total_seconds() / 3600.0)
        calendar_away: bool | None = None
        if calendar:
            st = self.hass.states.get(calendar)
            if st is not None and st.state in ("on", "off"):
                keyword = (self._cfg_str(CONF_AUTO_AWAY_KEYWORD) or DEFAULT_AUTO_AWAY_KEYWORD).lower()
                text = " ".join(str(st.attributes.get(k, "")) for k in ("message", "description")).lower()
                calendar_away = st.state == "on" and keyword in text
        decision = auto_away(
            homes, calendar_away,
            float(cfg.get(CONF_AUTO_AWAY_MIN_HOURS, DEFAULT_AUTO_AWAY_MIN_HOURS)), hours,
        )
        if decision is None or decision == bool(self.coordinator.state.away_mode):
            return
        self.coordinator.update_state(away_mode=decision)
        await self.async_notify(
            "🔋 Sunsynk: away mode " + ("on" if decision else "off"),
            "Switched automatically from presence/calendar. The away switch still overrides this until the next change.",
        )

    async def async_capture_meter_snapshot(self, time: str) -> None:
        """Log the cumulative daily meters at a Flux band edge (phase 14).

        Read-only, so it also runs in monitor mode. Unavailable meters log
        None, so the KPIs that need them come out None, never 0.
        """
        # Close the over-limit interval at the band edge so it lands in the right segment.
        await self._guarded(self._async_track_over_limit_import, "Over-limit import tracking")
        soc, soc_cached = recent_soc(self._read_soc(), self._last_valid_soc, dt_util.utcnow())
        await self.data_logger.async_log_meter_snapshot(
            date=dt_util.now().date().isoformat(),
            time=time,
            grid_import_kwh=round_or_none(self._essential_state(self.day_grid_import_entity)),
            grid_export_kwh=round_or_none(self._essential_state(self.day_grid_export_entity)),
            load_kwh=round_or_none(self._essential_state(self.day_load_entity)),
            soc=round_or_none(soc, 1),
            soc_from_cache=soc_cached,
        )

    async def _async_capture_day_kpis(self, date: str) -> dict[str, Any]:
        """22:00: close the day's snapshots and log the efficiency KPIs (phase 14)."""
        await self.async_capture_meter_snapshot("22:00")
        snapshots = await self.data_logger.async_load_meter_snapshots(date)
        cfg = self.cfg
        plan = self.coordinator.state.nightly_import_plan or {}
        late_load_kw = plan.get("avg_consumption_kw") if plan.get("date") == date else None
        if late_load_kw is None:
            late_load_kw = float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))
        prices, sources = self._tariff_prices(dt_util.parse_date(date))
        free_credit = free_event_kpi_credit(
            self.coordinator.state.free_event, dt_util.parse_date(date), self._capacity_kwh(cfg)
        )
        kpis = day_kpis(
            snapshots,
            prices,
            self._capacity_kwh(cfg),
            late_load_kw,
            free_credit,
            self._over_limit.get("kwh") if self._over_limit.get("date") == date else None,
        )
        record = {
            "date": date,
            "version": INTEGRATION_VERSION,
            "snapshot_times": sorted(snapshots),
            **kpis,
            "price_source": price_source(sources, *TARIFF_BANDS),
            "evening_reserve_soc": self._evening_reserve_soc(),
            "evening_load_kw_learned": self._evening_load_kw,
            "evening_reserve_soc_learned": self._evening_reserve_pair()[1],
            # Phase 71: the profile's reserve (shadow unless load_profile_live) and today's mean outside °C.
            "evening_reserve_soc_profile": evening_reserve_soc(
                self._load_profile["evening_kw"], self._capacity_kwh(cfg)
            ) if self._load_profile else None,
            "outside_temp_c": round(self._outside_temp["sum"] / self._outside_temp["n"], 1)
            if self._outside_temp.get("date") == date and self._outside_temp.get("n") else None,
            "wear_cost_p": self._wear_pence(),
            # Phase 37: what the day cost with vs without the battery.
            **no_battery_baseline(
                snapshots, prices, flux_bands_from_charges(cfg.get(CONF_CHARGES, []), "export"),
                self._capacity_kwh(cfg), self._wear_pence(), free_credit,
            ),
            # Shadow: would an 85% cap on a low-solar night have cost anything? Free-event days skew SOC.
            "soc_cap_shadow": None if kpis["free_import_excluded_kwh"] else soc_cap_shadow(
                plan if plan.get("date") == date else {}, snapshots, self._capacity_kwh(cfg), late_load_kw, prices,
            ),
        }
        await self.data_logger.async_log_day_kpis(**record)
        causes = self._import_causes if self._import_causes.get("date") == date else {}
        total = round(sum((causes.get("kwh") or {}).values()), 2)
        await self.data_logger.async_log_import_causes(
            date=date, **{c: round((causes.get("kwh") or {}).get(c, 0.0), 2) for c in IMPORT_CAUSES if c != "unknown"},
            unknown=round((causes.get("kwh") or {}).get("unknown", 0.0), 2), attributed_kwh=total,
            grid_import_kwh=(snapshots.get("22:00") or {}).get("grid_import_kwh"),
        )
        self.coordinator.update_state(touch=False, last_day_kpis=record)
        return {"type": "day_kpis", **record}

    async def _async_outside_temp_by_date(self, days: int) -> dict[str, float]:
        """{date: mean outside temperature °C} from the logged day_kpis records of the last ``days`` days."""
        return {
            r["date"]: r["outside_temp_c"] for r in await self.data_logger.async_load_records(days)
            if r.get("type") == "day_kpis" and r.get("date") and isinstance(r.get("outside_temp_c"), (int, float))
        }

    def _outside_temp_now(self) -> float | None:
        """Current outside °C from the weather entity's temperature attribute; None when unavailable."""
        state = self.hass.states.get(self._cfg_str(CONF_WEATHER_ENTITY) or DEFAULT_WEATHER_ENTITY)
        value = state.attributes.get("temperature") if state is not None else None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def _async_track_outside_temp(self) -> None:
        """30-min: running mean of today's outside temperature, logged on day_kpis (phase 71)."""
        temp = self._outside_temp_now()
        today = dt_util.now().date().isoformat()
        if self._outside_temp.get("date") != today:
            self._outside_temp = {"date": today, "sum": 0.0, "n": 0}
        if temp is not None:
            self._outside_temp["sum"] += temp
            self._outside_temp["n"] += 1

    def _profile_live(self) -> dict[str, Any] | None:
        """The load profile when `load_profile_live` is on and one has been learned; else None."""
        if not bool(self.cfg.get(CONF_LOAD_PROFILE_LIVE, DEFAULT_LOAD_PROFILE_LIVE)):
            return None
        return self._load_profile

    def _read_daily_meters(self) -> dict[str, float | None]:
        """SolarSynkV3 cumulative daily load / grid import / grid export (kWh); None when unavailable."""
        return {
            "load": self._essential_state(self.day_load_entity),
            "grid_import": self._essential_state(self.day_grid_import_entity),
            "grid_export": self._essential_state(self.day_grid_export_entity),
        }

    async def _async_capture_morning_state(self, _now) -> None:
        await self._guarded(self._async_do_capture_morning_state, "Morning capture")
        await self._guarded(self._async_capture_daily_cost, "Daily cost capture")

    async def _async_do_capture_morning_state(self) -> None:
        """Capture SOC and PV power at 06:00 to measure overnight battery drain.

        Also captures the SolarSynkV3 daily load total, which at 06:00 equals
        real 00:00-06:00 household consumption — a direct energy figure to
        cross-check against the SOC-derived overnight_drain_pct.
        """
        soc = self._essential_state(self.battery_soc_entity)
        pv_power = (
            self._state_float(self.pv_mppt0_entity, 0)
            + self._state_float(self.pv_mppt1_entity, 0)
        )
        overnight_load_kwh = self._essential_state(self.day_load_entity)
        date = dt_util.now().date().isoformat()
        await self.data_logger.async_log_morning_state(
            date=date,
            morning_soc=soc,
            morning_pv_power=pv_power,
            overnight_load_kwh=overnight_load_kwh,
        )
        self.coordinator.update_state(
            touch=False,
            last_morning_state={
                "type": "morning_state",
                "date": date,
                "morning_soc": round_or_none(soc, 1),
                "morning_pv_power": round(pv_power, 1),
                "overnight_load_kwh": round_or_none(overnight_load_kwh),
            },
        )

    async def _async_capture_daily_cost(self) -> None:
        """Read the optional Octopus cost sensors and log the settled day's cost.

        Runs as part of both the 06:00 and 22:00 captures, since Octopus's
        "previous accumulative cost" sensor doesn't reliably settle a few
        hours after midnight on every account — some settle as late as
        ~22:00 the following day (observed in practice). The 06:00 attempt
        catches accounts that settle overnight; the 22:00 attempt catches
        the late-settling ones in time for the same evening's debug bundle.
        Also recomputes the running year-to-date net cost from the full
        paired-day history, so that figure stays fresh without a dedicated
        scheduled listener.

        Each sensor's reading is filed under the date it actually reports
        (up to _OCTOPUS_CATCH_UP_DAYS back), and merged into that day's
        record, so a gas reading that settles a day after electricity still
        completes the right day. Merging only fills unset fields, so calling
        this twice a day is safe.
        """
        await self._guarded(self._async_capture_half_hourly, "Half-hourly capture")
        readings = self._read_octopus_previous_day_cost()
        if not readings:
            return  # No Octopus sensors configured/available — nothing to log.

        shown = self.coordinator.state.last_daily_cost or {}
        for date in sorted(readings):
            record = await self.data_logger.async_merge_daily_cost(date, readings[date])
            if record is None:
                continue  # Nothing new for this date — already fully logged.
            # Only a newer day (or a fuller copy of the day shown) replaces
            # the dashboard/debug-bundle figure; a catch-up for an older day
            # must not displace it.
            if date < str(shown.get("date") or ""):
                continue
            shown = {
                "type": "daily_cost",
                "date": date,
                **{k: record.get(k) for k in DAILY_COST_FIELDS},
                "net_cost_gbp": net_cost_gbp(*(record.get(k) for k in DAILY_COST_FIELDS)),
            }
            self.coordinator.update_state(touch=False, last_daily_cost=shown)

        # Recompute year-to-date net cost from the full paired-day history.
        # 366 days comfortably covers a rolling year within the 13-month
        # retention window; filtering to the current calendar year below is
        # what actually bounds it to "this year", not the day count.
        now = dt_util.now()
        paired_days = await self.data_logger.async_load_paired_days(days=366)
        self.coordinator.update_state(
            touch=False,
            last_complete_daily_cost=latest_complete_cost_day(paired_days, _octopus_oldest_date(now)) or {},
        )
        year_days = [
            d for d in paired_days
            if d.get("net_cost_gbp") is not None and str(d.get("date", "")).startswith(str(now.year))
        ]
        if year_days:
            self.coordinator.update_state(
                touch=False,
                last_year_to_date_cost={
                    "year": now.year,
                    "net_cost_gbp": sum_field(year_days, "net_cost_gbp"),
                    "days_counted": len(year_days),
                    "as_of_date": (now - timedelta(days=1)).date().isoformat(),
                },
            )

    async def _async_capture_half_hourly(self) -> None:
        """Log the settled day's 48 half-hourly import/export kWh (phase 28), if complete.

        Import and export settle on different days, so each is logged under its own
        date; an incomplete day (not 48 numeric slots) is skipped, never part-logged.
        """
        for kind, conf_key in (
            ("import", CONF_OCTOPUS_IMPORT_CONSUMPTION_SENSOR),
            ("export", CONF_OCTOPUS_EXPORT_CONSUMPTION_SENSOR),
        ):
            sensor_id = self._cfg_str(conf_key)
            state = self._available_state(sensor_id) if sensor_id else None
            parsed = parse_half_hourly_charges(state.attributes) if state is not None else None
            if parsed is not None:
                await self.data_logger.async_log_half_hourly(kind, parsed[0], parsed[1])
        await self._async_reconcile_meters()

    async def _async_reconcile_meters(self) -> None:
        """Compare inverter grid kWh with settled smart-meter slots; alert on 3 diverged days (phase 44)."""
        actuals, recon_import, recon_export = await self.data_logger.async_load_reconciliation_inputs()
        half_import, half_export = await self.data_logger.async_load_half_hourly(30)
        for kind, slots_by_date, existing, field in (
            ("import", half_import, recon_import, "day_grid_import_kwh"),
            ("export", half_export, recon_export, "day_grid_export_kwh"),
        ):
            history = [{"date": d, "diverged": bool(r.get("diverged"))} for d, r in existing.items()]
            newest = None
            for date, slots in sorted(slots_by_date.items()):
                if date in existing or date not in actuals:
                    continue
                result = meter_reconciliation(actuals[date].get(field), slots)
                if result is None:
                    continue
                await self.data_logger.async_log_meter_reconciliation(kind, date, **result)
                history.append({"date": date, "diverged": result["diverged"]})
                newest = date
            if newest is not None and divergence_streak(history) == 3:
                await self.async_notify(
                    f"🔋 Sunsynk: {kind} meter mismatch",
                    f"The inverter's grid {kind} has differed from your smart meter by more than 8% for 3 days in a row. "
                    "Check the CT clamp position/orientation, or compare with your Octopus bill.",
                )

    async def _async_capture_day_actuals(self, _now) -> None:
        await self._guarded(self._async_do_capture_day_actuals, "Day actuals capture")
        # Until a first estimate exists, re-measure after the 22:00 snapshot too.
        if capacity_estimate_pending(self.coordinator.state.battery_capacity_estimate):
            await self._guarded(lambda: self.async_run_capacity_estimate(notify=False), "Battery capacity estimate")

    async def _async_do_capture_day_actuals(self) -> None:
        """Capture end-of-day actuals at 22:00 and log them.

        Includes the SolarSynkV3 daily load/grid totals alongside SOC and solar,
        so consumption analysis doesn't have to infer household load from the
        SOC swing (which conflates load with solar availability) — the actual
        daily load figure is logged directly.
        """
        await self._guarded(self._async_auto_away, "Auto away check")
        # Unavailable meters log None, not 0 — a zero would skew learning.
        soc = self._essential_state(self.battery_soc_entity)
        actual_solar_kwh = self._essential_state(self.day_pv_energy_entity)
        day_load_kwh = self._essential_state(self.day_load_entity)
        day_grid_import_kwh = self._essential_state(self.day_grid_import_entity)
        day_grid_export_kwh = self._essential_state(self.day_grid_export_entity)
        now = dt_util.now()
        date = now.date().isoformat()
        evening_export_disabled = self.coordinator.state.evening_export_disabled
        clipped = self._clipped if self._clipped.get("date") == date else {}
        clipped_kwh = clipped.get("kwh") if clipped.get("periods") else (0.0 if clipped else None)
        too_high = overnight_target_too_high(clipped_kwh, clipped.get("first_full_hour"))
        derated = self._derated if self._derated.get("date") == date else {}
        derated_min = derated.get("minutes") if derated.get("seen") else None  # None: voltage never read
        await self.data_logger.async_log_day_actuals(
            date=date,
            evening_soc=soc,
            actual_solar_kwh=actual_solar_kwh,
            evening_export_disabled=evening_export_disabled,
            day_load_kwh=day_load_kwh,
            day_grid_import_kwh=day_grid_import_kwh,
            day_grid_export_kwh=day_grid_export_kwh,
            clipped_solar_kwh=clipped_kwh,
            overnight_target_too_high=too_high,
            export_derated_min=derated_min,
        )
        # After day_actuals is safely logged; before the bundle so its cost lines are fresh.
        await self._guarded(self._async_capture_daily_cost, "Daily cost capture")
        actuals_rec = {
            "type": "day_actuals",
            "date": date,
            "evening_soc": round_or_none(soc, 1),
            "actual_solar_kwh": round_or_none(actual_solar_kwh),
            "evening_export_disabled": evening_export_disabled,
            "day_load_kwh": round_or_none(day_load_kwh),
            "day_grid_import_kwh": round_or_none(day_grid_import_kwh),
            "day_grid_export_kwh": round_or_none(day_grid_export_kwh),
            "clipped_solar_kwh": round_or_none(clipped_kwh),
            "overnight_target_too_high": too_high,
            "export_derated_min": derated_min,
        }
        kpi_rec = await self._async_capture_day_kpis(date)
        floor_rec = await self._async_review_battery_floor(date)
        hourly_rec = await self._async_log_hourly_profile(date)
        await self._guarded(lambda: self._async_capture_pv_strings(date), "PV string capture")
        health_rec: dict[str, Any] = {}

        async def _health() -> None:
            nonlocal health_rec
            health_rec = await self._async_capture_daily_health(date)

        await self._guarded(_health, "Daily health")
        # Persisted (not just logged/notified) so the dashboard's Consumption
        # sensor has today's actuals to display, the same way last_morning_state
        # and last_peak_window_usage are kept for their own dashboard fields.
        self.coordinator.update_state(touch=False, last_day_actuals=actuals_rec)
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            plan_recs = daily_report_plans(
                self.coordinator.state.nightly_import_plan or {},
                self.coordinator.state.last_import_plan or {},
                date,
            )
            morning_rec = self.coordinator.state.last_morning_state or {}
            # Only include today's peak-window record — a stale prior-day value
            # (e.g. the window was never captured today because of a restart)
            # shouldn't be re-sent under today's date.
            peak_rec = self.coordinator.state.last_peak_window_usage or {}
            if peak_rec.get("date") != date:
                peak_rec = {}
            peak_export_rec = record_for_date(self.coordinator.state.peak_export, date)
            cover = (self._peak_cover or {}).get("record") or {}
            if cover.get("date") == date:
                peak_cover_rec = {"type": "peak_cover", **cover}
            else:
                # Unpersisted: a restart after 16:00 loses it, so fall back to the log.
                peak_cover_rec = await self.data_logger.async_load_peak_cover(date)
            # daily_cost is captured this morning tagged with YESTERDAY's date
            # (Octopus settlement lag — see _async_capture_daily_cost), so it's
            # inherently a day behind `date` here, not equal to it. Include it
            # as long as it isn't stale (older than yesterday), which would mean
            # the Octopus sensors were unavailable for a stretch. Self-describing
            # via its own `date` field so it's never confused with today's data.
            cost_rec = self.coordinator.state.last_daily_cost or {}
            yesterday_str = (now - timedelta(days=1)).date().isoformat()
            if cost_rec.get("date") not in (date, yesterday_str):
                cost_rec = {}
            # Newest fully settled day (import/gas usually lag export by a
            # day or more). Skipped when cost_rec already shows that day whole.
            complete_rec = self.coordinator.state.last_complete_daily_cost or {}
            if str(complete_rec.get("date") or "") < _octopus_oldest_date(now) or (
                complete_rec.get("date") == cost_rec.get("date") and cost_rec.get("net_cost_gbp") is not None
            ):
                complete_rec = {}
            lines = "\n".join(
                json.dumps(r)
                for r in [health_rec, *plan_recs, morning_rec, actuals_rec, kpi_rec, floor_rec, hourly_rec, peak_rec, peak_cover_rec, peak_export_rec, cost_rec, complete_rec]
                if r
            )
            await self.async_notify_debug(
                f"Sunsynk Daily Data — {date}",
                lines,
                target=data_report_target,
            )

    async def _async_report_event(self, phase: str, event: dict[str, Any]) -> None:
        """Send a free event lifecycle line to the debug channel (blank target = off)."""
        target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if target:
            await self.async_notify_debug("Sunsynk Free Event", json.dumps(event_report("free_event", phase, event)), target=target)
