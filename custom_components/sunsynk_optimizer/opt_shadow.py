# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: MPC, dynamic tariff, native TOU, newsvendor, multi-day outlook shadows."""
from __future__ import annotations

import json
from datetime import (
    datetime,
    timedelta,
)
from typing import Any
import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from .const import (
    CONF_AVG_CONSUMPTION_KW,
    CONF_CHARGE_RATE,
    CONF_CHARGES,
    CONF_DATA_REPORT_TARGET,
    CONF_FREE_EVENT_EXPORT_RATE_KW,
    CONF_FLEX_EVENT_ENTITY,
    CONF_FLEX_PRICE_PENCE,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_TARIFF_RATE_OVERRIDES,
    CONF_OCTOPUS_IMPORT_RATES_ENTITY,
    CONF_OCTOPUS_EXPORT_RATES_ENTITY,
    CONF_TOMORROW_FORECAST_SENSOR,
    CONF_PV_KWP,
    CONF_PV_TILT,
    CONF_PV_AZIMUTH,
    CONF_PV2_TILT,
    CONF_PV2_AZIMUTH,
    CONF_PV2_SHARE_PCT,
    DEFAULT_PV2_SHARE_PCT,
    DEFAULT_AVG_CONSUMPTION_KW,
    DEFAULT_CHARGE_RATE,
    DEFAULT_TOMORROW_FORECAST_SENSOR,
)
from .flux_helpers import (
    TARIFF_BANDS,
    octopus_rate_pence_per_kwh,
    tariff_prices_pence,
)
from .octopus_public import async_fetch_agile_rates
from .open_meteo import ARCHIVES, async_fetch_ensemble, async_fetch_tilted, async_fetch_tilted_archive
from .planning import (
    flux_bands_from_charges,
    merge_tariff_rates,
    agile_prices_by_date,
    BATTERY_LOW_SOC,
    cheapest_window_plan,
    flex_event_offer,
    flex_event_window,
    tou_diff,
    tou_slots_from_plan,
    house_peak_kw,
    tou_inverter_powers,
    tou_slot_powers,
    tou_slots_from_settings,
    dynamic_tariff_summary,
    MPC_MAX_SLOTS,
    MPC_SLOT_MINUTES,
    tariff_vat_percent,
    MULTI_DAY_NEED_HOURS,
    low_solar_run,
    multi_day_peak_floor_soc,
    multi_day_shadow,
    CHARGE_EFFICIENCY,
    mpc_slots,
    scale_hourly,
    mpc_solve,
    mpc_summary,
    CHEAP_WINDOW_HOURS,
    apply_soc_adjustments,
    newsvendor_costs,
    newsvendor_quantile,
    newsvendor_target,
    quantile_correction,
    OM_ARCHIVE_START,
    OM_BACKFILL_DAYS,
    OM_HISTORY_DAYS,
    compass_to_open_meteo,
    om_archive_days,
    om_archive_training_hours,
    om_backtest,
    om_ensemble_band,
    om_split_backfill,
    stats_hourly_kwh,
    om_cloud_hours,
    om_learn,
    om_predict,
    om_reference_hours,
    om_solar_start,
    om_strings,
    om_backfill_missing,
    om_training_hours,
)

_LOGGER = logging.getLogger(__name__)


class ShadowMixin:
    """MPC, dynamic tariff, native TOU, newsvendor, multi-day outlook shadows (methods of SunsynkOptimizer)."""

    def _mpc_loads(self, cfg: dict[str, Any]) -> dict[str, float]:
        """Per-band house load kW for the MPC: tonight's plan rate, the learned evening rate, the config day rate."""
        overnight = self._plan_load_kw(cfg)
        day = float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))
        return {"overnight": overnight, "day": day, "evening": self._evening_load_kw or day}

    def _mpc_inputs(self, cfg: dict[str, Any], now: datetime) -> dict[str, Any]:
        """The inputs both MPC shadows solve from (phases 69 and 76): forecasts, prices, loads, battery and end value."""
        tomorrow_id = self._cfg_str(CONF_TOMORROW_FORECAST_SENSOR, cfg) or DEFAULT_TOMORROW_FORECAST_SENSOR
        tomorrow_kwh = self._essential_state(tomorrow_id)
        prices, _sources = self._tariff_prices(now.date())
        offpeak = prices.get("offpeak")
        return {
            "tomorrow_kwh": tomorrow_kwh,
            "hourly_tomorrow": None if tomorrow_kwh is None else (self._synthetic_hourly_forecast(tomorrow_kwh) or {}),
            "hourly_today": self._hourly_or_synthetic_forecast(cfg, now),
            "prices": prices,
            "loads": self._mpc_loads(cfg),
            "export_bands": flux_bands_from_charges(cfg.get(CONF_CHARGES, []), "export"),
            "capacity": self._capacity_kwh(cfg),
            "charge_kw": float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE)),
            "wear": self._wear_pence(),
            "terminal": None if offpeak is None else offpeak / CHARGE_EFFICIENCY,
        }

    async def async_run_mpc_shadow(self) -> None:
        """Every 30 min: solve the 24 h MPC plan and log it next to the rule plan (phase 69); never pushes."""
        cfg = self.cfg
        now = dt_util.now()
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            return
        mpc = self._mpc_inputs(cfg, now)
        tomorrow_kwh, hourly_today, hourly_tomorrow, prices = mpc["tomorrow_kwh"], mpc["hourly_today"], mpc["hourly_tomorrow"], mpc["prices"]
        export_bands, loads, capacity, charge_kw, wear, terminal = (
            mpc["export_bands"], mpc["loads"], mpc["capacity"], mpc["charge_kw"], mpc["wear"], mpc["terminal"]
        )

        def _solve(ratio: float | None) -> dict[str, Any] | None:
            slots = mpc_slots(
                now.hour * 60 + now.minute, prices, export_bands, scale_hourly(hourly_today, ratio),
                scale_hourly(hourly_tomorrow, ratio), loads, (self._load_profile or {}).get("slots"),
            )
            return None if slots is None else mpc_solve(
                slots, soc, capacity, charge_kw, wear_pence=wear, terminal_pence=terminal
            )

        plan = await self.hass.async_add_executor_job(_solve, None)
        if plan is None:
            return
        # Phase 70: the same plan with today's learned P10 / P90 solar (shadow robustness check).
        last = self.coordinator.state.last_import_plan or {}
        quantiles = (last.get("forecast_quantiles") or {}) if last.get("date") == now.date().isoformat() else {}
        spread: dict[str, Any] = {}
        for key in ("p10", "p90"):
            ratio = quantiles.get(f"{key}_ratio")
            alt = await self.hass.async_add_executor_job(_solve, ratio) if ratio is not None else None
            alt_summary = mpc_summary(alt)
            spread[f"soc_0500_{key}"] = alt_summary.get("soc_0500")
            spread[f"first_action_{key}"] = alt_summary.get("first_action")
            spread[f"cost_gbp_{key}"] = alt_summary.get("cost_gbp")
        rule = self.coordinator.state.nightly_import_plan or {}
        record = {
            "date": now.date().isoformat(), "time": now.strftime("%H:%M"), "soc": soc, **mpc_summary(plan), **spread,
            "rule_plan_date": rule.get("date"), "rule_target_soc": rule.get("target_soc"),
            "loads_kw": loads, "load_profile_days": (self._load_profile or {}).get("days"),
            "tomorrow_solar_kwh": tomorrow_kwh,
        }
        await self.data_logger.async_log_mpc_plan(**record)
        self.coordinator.update_state(touch=False, mpc_plan={**record, "slots": plan["slots"]})

    async def async_run_flex_event_shadow(self) -> None:
        """While the flex event entity is on: log once per event what the battery could offer (phase 77); no export."""
        entity = self._cfg_str(CONF_FLEX_EVENT_ENTITY)
        state = self.hass.states.get(entity) if entity else None
        if state is None or state.state != "on":
            return
        cfg, now = self.cfg, dt_util.now()
        start, hours = flex_event_window(dict(state.attributes), now, dt_util.DEFAULT_TIME_ZONE)
        # Phase 129: dedup on the entity's own on/off transition, not the computed display time. A plain
        # on/off entity (no start_time attribute) reports `start` as "now", which changes every 30-min
        # poll and never matched the old key — so this logged on every poll for the whole event, not once.
        key = f"{entity}:{state.last_changed.isoformat()}"
        if self._flex_event_logged == key:
            return
        self._flex_event_logged = key
        try:
            price = float(cfg.get(CONF_FLEX_PRICE_PENCE))
        except (TypeError, ValueError):
            price = None
        prices, _sources = self._tariff_prices(now.date())
        charge_kw = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        load_kw = self._evening_load_kw or float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))
        soc = self._essential_state(self.battery_soc_entity)
        offer = flex_event_offer(
            soc, self._evening_reserve_soc(), self._capacity_kwh(cfg), hours, load_kw, price, prices.get("offpeak"),
            float(cfg.get(CONF_FREE_EVENT_EXPORT_RATE_KW) or charge_kw), self._wear_pence(), self._slots_held(),
        )
        record = {
            "date": now.date().isoformat(), "time": start, "entity": entity, "hours": hours, "soc": soc,
            "price_p": price, "house_load_kw": round(load_kw, 2), **offer,
        }
        await self.data_logger.async_log_flex_event(**record)
        self.coordinator.update_state(touch=False, flex_event=record)
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Flex Event (shadow)", json.dumps({"type": "flex_event", **record}), target=data_report_target
            )

    async def async_run_tou_shadow(self, plan: dict[str, Any]) -> None:
        """After the plan: the six inverter TOU slots it would need vs what the inverter holds (phase 73).

        Read-only (also in monitor mode); never writes the slots."""
        flux1 = (plan.get("payload") or {}).get("flux_1") or {}
        proposed = tou_slots_from_plan(
            flux1.get("startTime"), flux1.get("endTime"), plan.get("target_soc"), BATTERY_LOW_SOC,
            self._evening_reserve_soc(),
        )
        settings = await self._async_api_read(
            "Inverter settings", self.coordinator.api.async_read_settings, self.inverter_serial
        )
        current = tou_slots_from_settings(settings)
        diffs = tou_diff(current, proposed)
        # Phase 115 (shadow): the per-slot power limits the plan would set. The peak export is only
        # known at 16:00, so peak slots read None here; nothing is written.
        peak_kw = house_peak_kw(await self.data_logger.async_load_records(14))
        powers = tou_slot_powers(proposed, self._evening_load_kw, peak_kw)
        inverter_powers = tou_inverter_powers(settings)
        now = dt_util.now()
        record = {
            "date": now.date().isoformat(), "time": now.strftime("%H:%M"), "target_soc": plan.get("target_soc"),
            "proposed": proposed, "inverter": current, "readable": current is not None,
            "diffs": diffs, "diff_count": None if diffs is None else len(diffs),
            "power_w": powers, "inverter_power_w": inverter_powers, "house_peak_kw": peak_kw,
        }
        await self.data_logger.async_log_tou_shadow(**record)
        self.coordinator.update_state(touch=False, tou_shadow=record)

    async def _async_dynamic_tariff_shadow(self, _now) -> None:
        await self._guarded(self.async_run_dynamic_tariff_shadow, "Dynamic tariff shadow")

    async def _async_agile_day_vectors(self, region: str, first) -> dict[int, list[float]]:
        """{0: today's, 1: tomorrow's} 48 Agile p/kWh (VAT applied) from the public API; {} on any failure."""
        start = dt_util.start_of_local_day(first)
        end = dt_util.start_of_local_day(first + timedelta(days=2))
        results = await async_fetch_agile_rates(async_get_clientsession(self.hass), region, start, end)
        by_date = agile_prices_by_date(results, dt_util.DEFAULT_TIME_ZONE)
        vat = 1 + tariff_vat_percent(self.cfg.get(CONF_TARIFF_RATE_OVERRIDES)) / 100
        out = {}
        for offset in (0, 1):
            day = by_date.get((first + timedelta(days=offset)).isoformat())
            if day:
                out[offset] = [round(p * vat, 3) for p in day]
        return out

    async def async_run_dynamic_tariff_shadow(self) -> None:
        """22:05: the next 24 h MPC priced on Agile vs on Flux (phase 76); logs only, never pushes."""
        cfg = self.cfg
        now = dt_util.now()
        overrides = cfg.get(CONF_TARIFF_RATE_OVERRIDES)
        region = self._agile_region(overrides)
        base = {"date": now.date().isoformat(), "time": now.strftime("%H:%M"), "region": region}
        soc = self._essential_state(self.battery_soc_entity)
        vectors = await self._async_agile_day_vectors(region, now.date()) if region and soc is not None else {}
        if soc is None or not vectors.get(0):
            reason = "no_soc" if soc is None else ("no_region" if not region else "no_prices")
            record = {**base, "skipped": reason}
            await self.data_logger.async_log_dynamic_tariff(**record)
            self.coordinator.update_state(touch=False, dynamic_tariff=record)
            return
        mpc = self._mpc_inputs(cfg, now)
        hourly_today, hourly_tomorrow, prices = mpc["hourly_today"], mpc["hourly_tomorrow"], mpc["prices"]
        export_bands, loads, capacity, charge_kw, wear, terminal = (
            mpc["export_bands"], mpc["loads"], mpc["capacity"], mpc["charge_kw"], mpc["wear"], mpc["terminal"]
        )
        profile = (self._load_profile or {}).get("slots")  # terminal: same end value on both sides
        agile_export = merge_tariff_rates(overrides)["agile"]["export"]
        start = now.hour * 60 + now.minute
        # Phase 129: without tomorrow's Agile prices, mpc_slots stops the Agile horizon at midnight
        # tonight (no day-1 vector); the Flux side must stop there too, or saving_gbp compares a
        # ~24h Flux total against a shorter Agile one.
        tomorrow_for_slots = hourly_tomorrow if vectors.get(1) is not None else None

        def _solve(agile: bool) -> dict[str, Any] | None:
            slots = mpc_slots(
                start, prices, export_bands, hourly_today, tomorrow_for_slots, loads, profile,
                import_vectors=vectors if agile else None, export_flat_p=agile_export if agile else None,
            )
            return None if slots is None else mpc_solve(slots, soc, capacity, charge_kw, wear_pence=wear, terminal_pence=terminal)

        flux_plan = await self.hass.async_add_executor_job(_solve, False)
        agile_plan = await self.hass.async_add_executor_job(_solve, True)
        horizon = (vectors.get(0) or []) + (vectors.get(1) or [])
        from_slot = start // MPC_SLOT_MINUTES
        need = mpc_summary(flux_plan).get("grid_charge_kwh") or 0.0
        windows = cheapest_window_plan(horizon, need, charge_kw, from_slot=from_slot)
        record = {
            **base, "soc": soc,
            **dynamic_tariff_summary(flux_plan, agile_plan, horizon[from_slot:from_slot + MPC_MAX_SLOTS], windows),
            "tomorrow_prices": vectors.get(1) is not None,
        }
        await self.data_logger.async_log_dynamic_tariff(**record)
        self.coordinator.update_state(touch=False, dynamic_tariff=record)

    async def _async_open_meteo_shadow(self, now, load_kw: float | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Phase 136 (shadow): today's Open-Meteo solar forecast through the learned sun and shading
        model. Returns (plan fields, om_forecast record fields). No panel config = ({}, None); a failed
        fetch = fields with om_basis "unavailable". Read-only: it never feeds the target."""
        cfg = self.cfg
        strings = om_strings(
            cfg.get(CONF_PV_KWP), cfg.get(CONF_PV_TILT), cfg.get(CONF_PV_AZIMUTH), cfg.get(CONF_PV2_TILT),
            cfg.get(CONF_PV2_AZIMUTH), cfg.get(CONF_PV2_SHARE_PCT, DEFAULT_PV2_SHARE_PCT),
        )
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        if not strings or lat is None or lon is None:
            return {}, None
        unavailable = {
            "om_forecast_kwh": None, "om_basis": "unavailable", "om_solar_start": None, "om_days": None,
            "om_cloud_pct": None,
        }
        session = async_get_clientsession(self.hass)
        responses = []
        for i, (tilt, azimuth, _kwp) in enumerate(strings):
            response = await async_fetch_tilted(session, lat, lon, tilt, compass_to_open_meteo(azimuth), cloud=i == 0)
            if response is None or (i and response["time"] != responses[0]["time"]):
                return unavailable, None
            responses.append(response)
        tz, day = dt_util.DEFAULT_TIME_ZONE, now.date()
        times = responses[0]["time"]
        ref = om_reference_hours(times, [r["gti"] for r in responses], [s[2] for s in strings], day, tz)
        if not ref:
            return unavailable, None
        cloud = om_cloud_hours(times, responses[0].get("cloud") or [], day, tz)
        records = await self.data_logger.async_load_records(OM_HISTORY_DAYS + 1)
        hours = om_training_hours(
            [r for r in records if r.get("type") == "om_forecast"],
            [r for r in records if r.get("type") == "hourly_profile"], lat, lon, tz, day,
        )
        backfill = (await self.data_logger.async_load_om_backfill()).get("hours") or []
        window, older = om_split_backfill(backfill, {h["date"] for h in hours}, day)  # phase 138
        model = om_learn([*hours, *window], older)
        pred = om_predict(ref, cloud, model, lat, lon, day, tz)
        daylight = [cloud[h] for h in ref if ref[h] > 0 and h in cloud]
        fields = {
            "om_forecast_kwh": pred["kwh"], "om_basis": pred["basis"],
            "om_solar_start": om_solar_start(pred["hourly"], load_kw), "om_days": model["days"],
            "om_cloud_pct": round(sum(daylight) / len(daylight), 1) if daylight else None,
        }
        fields.update(await self._async_om_ensemble(session, strings, lat, lon, day, tz, pred["kwh"]))  # phase 140
        record = {
            "date": day.isoformat(), "kwh": pred["kwh"], "basis": pred["basis"],
            "strings": [{"tilt": t, "azimuth": a, "kwp": k} for t, a, k in strings],
            "hours": [
                {"hour": h, "ref_kwh": ref[h], "cloud_pct": cloud.get(h), "kwh": pred["hourly"].get(h)}
                for h in sorted(ref) if ref[h] > 0
            ],
            "model": {
                "ratio_all": model["ratio_all"], "ratios": model["ratios"], "shade_bins": len(model["shade"]),
                "days": model["days"], "hours": model["hours"],
            },
        }
        return fields, record

    async def _async_om_ensemble(self, session, strings, lat, lon, day, tz, om_kwh) -> dict[str, Any]:
        """Phase 140 (shadow): the ensemble band fields; all None when the fetch fails or the
        runs do not line up across strings. Never feeds the target."""
        members, times = [], None
        if om_kwh is not None:
            for tilt, azimuth, _kwp in strings:
                r = await async_fetch_ensemble(session, lat, lon, tilt, compass_to_open_meteo(azimuth))
                if r is None or (times is not None and r["time"] != times):
                    members = []
                    break
                times = r["time"]
                members.append(r["members"])
        return om_ensemble_band(times or [], members, [s[2] for s in strings], day, tz, om_kwh)

    async def _async_om_backfill_startup(self, _now) -> None:
        await self._guarded(self.async_run_om_backfill, "Open-Meteo backfill")

    async def async_run_om_backfill(self, force: bool = False) -> None:
        """Phase 138: once (or on the button), train and backtest the Open-Meteo model on archived
        forecasts. Read-only: no inverter writes, never feeds the target. Hourly solar comes from HA's
        long-term statistics for the PV day counter; without them only the daily backtest runs."""
        if self.coordinator.state.om_backfilled and not force:
            return
        today = dt_util.now().date()
        try:
            await self._async_om_backfill_run(today)
        except Exception as err:  # phase 141: report it, then let _guarded set Last error
            try:
                await self._async_om_backfill_outcome(
                    {"date": today.isoformat(), "status": "error", "reason": str(err) or type(err).__name__},
                )
            except Exception:  # pragma: no cover - the original error matters more
                pass
            raise

    async def _async_om_backfill_outcome(self, summary: dict[str, Any], log: bool = True) -> None:
        """Phase 141: every backfill outcome goes to the debug stream and (with ``log``) the log file;
        the latest final status is kept in state for the diagnostics download."""
        if log:
            await self.data_logger.async_log_om_backfill(**summary)
        if summary.get("status") != "started":
            self.coordinator.update_state(touch=False, om_backfill_status={
                k: summary.get(k) for k in ("date", "status", "reason") if summary.get(k) is not None
            })
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Open-Meteo backfill (shadow)", json.dumps({"type": "om_backfill", **summary}),
                target=data_report_target,
            )

    async def _async_om_backfill_run(self, today) -> None:
        cfg = self.cfg
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        missing = om_backfill_missing(cfg.get(CONF_PV_KWP), cfg.get(CONF_PV_TILT), cfg.get(CONF_PV_AZIMUTH), lat, lon)
        strings = [] if missing else om_strings(
            cfg.get(CONF_PV_KWP), cfg.get(CONF_PV_TILT), cfg.get(CONF_PV_AZIMUTH), cfg.get(CONF_PV2_TILT),
            cfg.get(CONF_PV2_AZIMUTH), cfg.get(CONF_PV2_SHARE_PCT, DEFAULT_PV2_SHARE_PCT),
        )
        if missing or not strings:
            await self._async_om_backfill_outcome(
                {"date": today.isoformat(), "status": "not_configured", "reason": f"missing {missing or 'panel strings'}"},
            )
            return
        tz = dt_util.DEFAULT_TIME_ZONE
        records = await self.data_logger.async_load_records(OM_BACKFILL_DAYS + 1)
        actuals = {
            r["date"]: r["actual_solar_kwh"] for r in records
            if r.get("type") == "day_actuals" and isinstance(r.get("date"), str) and r.get("actual_solar_kwh") is not None
        }
        live = {
            r["date"]: r["solar_forecast_kwh"] for r in records
            if r.get("type") == "import_plan" and isinstance(r.get("date"), str) and r.get("solar_forecast_kwh") is not None
        }
        start = max(today - timedelta(days=OM_BACKFILL_DAYS), OM_ARCHIVE_START)
        end = today - timedelta(days=1)
        await self._async_om_backfill_outcome(
            {"date": today.isoformat(), "status": "started", "from": start.isoformat(), "to": end.isoformat()}, log=False,
        )
        pv = await self._async_pv_hourly_stats(start, end, tz)
        for d, hours in pv.items():
            if d not in actuals and len(hours) >= 20:  # a whole day of statistics stands in for day_actuals
                actuals[d] = round(sum(hours.values()), 2)
        days: dict[str, dict[str, dict[int, float]]] = {}
        archive = None
        session = async_get_clientsession(self.hass)
        chunk = start
        while chunk <= end:
            chunk_end = min(chunk + timedelta(days=30), end)
            span = [chunk + timedelta(days=i) for i in range((chunk_end - chunk).days + 1)]
            for name, url, suffix, lead in ARCHIVES if archive is None else [a for a in ARCHIVES if a[0] == archive[0]]:
                responses = []
                for i, (tilt, azimuth, _kwp) in enumerate(strings):
                    r = await async_fetch_tilted_archive(
                        session, url, suffix, lat, lon, tilt, compass_to_open_meteo(azimuth),
                        chunk.isoformat(), chunk_end.isoformat(), cloud=i == 0,
                    )
                    if r is None or (i and r["time"] != responses[0]["time"]):
                        responses = []
                        break
                    responses.append(r)
                if responses:
                    archive = (name, url, suffix, lead)
                    days.update(om_archive_days(
                        responses[0]["time"], [r["gti"] for r in responses], [s[2] for s in strings],
                        responses[0].get("cloud") or [], span, tz,
                    ))
                    break
            chunk = chunk_end + timedelta(days=1)
        summary: dict[str, Any] = {
            "date": today.isoformat(), "archive": archive[0] if archive else None,
            "lead_days": archive[3] if archive else None, "from": start.isoformat(), "to": end.isoformat(),
            "archive_days": len(days), "hourly_solar_days": len(pv),
        }
        if not days:
            await self._async_om_backfill_outcome({
                **summary, "days": 0, "status": "unavailable", "tried": [a[0] for a in ARCHIVES],
                "reason": "no archive returned data",
            })
            return
        hours = om_archive_training_hours(days, pv, lat, lon, tz)
        result = await self.hass.async_add_executor_job(om_backtest, days, hours, actuals, live, lat, lon, tz)
        await self.data_logger.async_save_om_backfill({
            "date": today.isoformat(), "archive": archive[0], "lead_days": archive[3], "hours": hours,
        })
        summary.update(result, status="ok", training_hours=len(hours), hourly=bool(hours))
        self.coordinator.update_state(touch=False, om_backfilled=today.isoformat())  # only ok marks it done
        await self._async_om_backfill_outcome(summary)

    async def _async_pv_hourly_stats(self, start, end, tz) -> dict[str, dict[int, float]]:
        """{date: {hour: kWh}} for the PV day counter from HA long-term statistics; {} when the
        recorder or the statistic is missing (the backfill then scores daily totals only)."""
        entity = self.day_pv_energy_entity
        if not entity:
            return {}
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period

            first = datetime.combine(start, datetime.min.time(), tzinfo=tz)
            last = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=tz)
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, first, last, {entity}, "hour", None, {"change"},
            )
        except Exception as exc:  # recorder not loaded, no statistics, API change
            _LOGGER.debug("PV statistics unavailable for the Open-Meteo backfill: %s", exc)
            return {}
        return stats_hourly_kwh(list((stats or {}).get(entity) or []), tz)

    def _newsvendor_shadow(
        self, decision, forecast_fallback: bool, paired_days: list[dict[str, Any]], today, band: str,
        avg_consumption_kw: float, battery_capacity_kwh: float, drain_adjustment: int, nudge: int,
    ) -> dict[str, Any]:
        """Phase 64 shadow fields; all None unless the hourly bridge ran with enough history and prices."""
        empty: dict[str, Any] = {
            "target_soc_newsvendor": None, "newsvendor_ratio": None, "newsvendor_quantile": None,
            "newsvendor_under_p": None, "newsvendor_over_p": None,
        }
        if decision.reason != "solar_bridge_hourly" or forecast_fallback:
            return empty
        prices, _sources = self._tariff_prices(today)
        costs = newsvendor_costs(prices.get("day"), prices.get("offpeak"), prices.get("export_peak"),
                                 wear_pence=self._wear_pence())
        if costs is None:
            return empty
        quantile = newsvendor_quantile(*costs)
        ratio, _days = quantile_correction(paired_days, today, quantile, band)
        bridge = newsvendor_target(self._get_hourly_forecast_kwh(), avg_consumption_kw, battery_capacity_kwh, ratio)
        if bridge is None:
            return empty
        target = bridge if bridge >= 100 else apply_soc_adjustments(bridge, drain_adjustment, nudge)
        return {
            "target_soc_newsvendor": target, "newsvendor_ratio": ratio, "newsvendor_quantile": quantile,
            "newsvendor_under_p": costs[0], "newsvendor_over_p": costs[1],
        }

    async def _async_multi_day_outlook(self, cfg: dict[str, Any]) -> tuple[int, float | None]:
        """(low-solar run length from today, tomorrow's pessimistic forecast kWh) — phase 39."""
        tomorrow_id = str(cfg.get(CONF_TOMORROW_FORECAST_SENSOR, DEFAULT_TOMORROW_FORECAST_SENSOR) or "").strip()
        raw = [
            self._essential_state(cfg[CONF_SOLAR_FORECAST_SENSOR]),
            self._essential_state(tomorrow_id) if tomorrow_id else None,
        ]
        paired_days = await self.data_logger.async_load_paired_days(days=30)
        correction = self.data_logger.compute_forecast_correction(paired_days)
        # Pessimistic of raw vs corrected, as for every low-solar decision (rule 8).
        days = [None if kwh is None else round(min(kwh, kwh * correction), 2) for kwh in raw]
        return low_solar_run(days), days[1]

    async def _async_log_multi_day_shadow(
        self, stage: str, target_soc: int | None = None, peak_target_soc: int | None = None, reserve: int = 0
    ) -> None:
        """Log what multi-day low-solar planning would change (phase 39, shadow only — no writes)."""
        cfg = self.cfg
        run_days, tomorrow_kwh = await self._async_multi_day_outlook(cfg)
        floor = None
        if peak_target_soc is not None:
            plan = self.coordinator.state.nightly_import_plan or {}
            rate, efficiency = plan.get("used_charge_rate_kw"), plan.get("charge_efficiency")
            refill = rate * CHEAP_WINDOW_HOURS * efficiency if rate is not None and efficiency is not None else None
            floor = multi_day_peak_floor_soc(
                reserve, self._capacity_kwh(cfg), self._plan_load_kw(cfg) * MULTI_DAY_NEED_HOURS, tomorrow_kwh, refill
            )
        shadow = multi_day_shadow(run_days, target_soc, peak_target_soc, floor)
        if shadow is None:
            return
        await self.data_logger.async_log_multi_day_plan(
            date=dt_util.now().date().isoformat(), stage=stage, tomorrow_solar_kwh=tomorrow_kwh,
            peak_floor_soc=floor, **shadow,
        )

    def _tariff_prices(self, on_date=None) -> tuple[dict[str, float | None], dict[str, str | None]]:
        """Band prices (p/kWh) + per-band source: Octopus rate entities first, then `charges` (phase 18)."""
        cfg = self.cfg
        return tariff_prices_pence(cfg.get(CONF_CHARGES, []), self._octopus_band_prices(on_date, cfg))

    def _octopus_band_prices(self, on_date=None, cfg=None) -> dict[str, float | None]:
        """Per-band p/kWh from the Octopus rate entities; bands without an entity are absent."""
        cfg = cfg or self.cfg
        entities = {
            "import": self._cfg_str(CONF_OCTOPUS_IMPORT_RATES_ENTITY, cfg),
            "export": self._cfg_str(CONF_OCTOPUS_EXPORT_RATES_ENTITY, cfg),
        }
        states = {status: self.hass.states.get(eid) if eid else None for status, eid in entities.items()}
        # The parse is the SOC-path cost (hundreds of ISO dates), so reuse it until a rates entity updates (phase 67).
        cache_key = (on_date, *((eid, getattr(states[status], "last_updated", None)) for status, eid in entities.items()))
        cached = self._band_price_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        octopus: dict[str, float | None] = {}
        for key, (start, end, status) in TARIFF_BANDS.items():
            state = states[status]
            if state is not None:
                octopus[key] = octopus_rate_pence_per_kwh(
                    state.state, dict(state.attributes), start, end, on_date, dt_util.DEFAULT_TIME_ZONE
                )
        if len(self._band_price_cache) >= 8:
            self._band_price_cache.clear()
        self._band_price_cache[cache_key] = dict(octopus)
        return octopus
