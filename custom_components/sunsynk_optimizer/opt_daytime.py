# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: SOC listener, Flux 2 check, trim, peak cover, peak export and re-checks."""
from __future__ import annotations

import logging
from datetime import (
    datetime,
    timedelta,
)
from typing import Any
from homeassistant.core import Event
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from .const import (
    CONF_EXPORT_LIMIT_W,
    CONF_CHARGE_RATE,
    CONF_CHARGES,
    CONF_EXPORT_DISABLE_THRESHOLD,
    CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
    CONF_COST_AWARE_EXPORT_SHADOW_MODE,
    CONF_PEAK_EXPORT_LIVE,
    CONF_SPARE_SOLAR_NOTIFY_KWH,
    CONF_PEAK_COVER_LIVE,
    CONF_TRIM_DEFER_LIVE,
    CONF_BASELOAD_ALERT,
    DEFAULT_BASELOAD_ALERT,
    DEFAULT_CHARGE_RATE,
    DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
    DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE,
    DEFAULT_PEAK_EXPORT_LIVE,
    DEFAULT_PEAK_COVER_LIVE,
    DEFAULT_TRIM_DEFER_LIVE,
)
from .flux_helpers import price_source
from .planning import (
    BATTERY_BOTTOM_SOC,
    grid_online_from_voltage,
    outage_record,
    outage_runtime_hours,
    outage_track,
    flux_bands_from_charges,
    CLIPPED_SOC_PCT,
    estimate_clipped_kwh,
    is_clipping,
    DAYTIME_IMPORT_AWAY_THRESHOLD_W,
    DAYTIME_IMPORT_CHECKS,
    DAYTIME_IMPORT_FROM_HOUR,
    DAYTIME_IMPORT_THRESHOLD_W,
    DAYTIME_IMPORT_UNTIL_HOUR,
    daytime_import_anomaly,
    cheapest_hour_today,
    marginal_cost_pence,
    solar_is_spare,
    VOLTAGE_DERATE_PERIOD_MIN,
    voltage_derate_suspect,
    record_for_date,
    SPARE_SOLAR_FIRST_HOUR,
    expected_spare_solar,
    PEAK_COVER_FROM_MINUTES,
    PEAK_COVER_HOLD_FROM_MINUTES,
    PEAK_COVER_LAST_LOG_MINUTES,
    PEAK_COVER_PEAK_START_MINUTES,
    daytime_load_kw,
    evening_reserve_soc,
    peak_cover_plan,
    peak_cover_startup_retry_due,
    peak_export_plan,
    peak_export_recheck,
    peak_export_skip_reason,
    peak_load_estimate_kwh,
    peak_load_estimate_split,
    peak_need_soc,
    over_limit_import_step,
    intraday_forecast_scale,
    scaled_solar_kwh,
    solar_until_kwh,
    import_cause_step,
    trim_defer_kwh,
    EXPORT_PAUSE_FLUX2_SLOT,
    carry_held_flux1,
)
from .opt_common import (
    _api_note,
    _reserve_note,
)

_LOGGER = logging.getLogger(__name__)

class DaytimeMixin:
    """SOC listener, Flux 2 check, trim, peak cover, peak export and re-checks (methods of SunsynkOptimizer)."""

    async def async_run_flux2_check(self, source: str = "automatic") -> None:
        """Run Flux 2 evening export / trim logic."""
        async with self._flux2_lock:
            notice = await self._async_flux2_decide(source)
        if notice:
            await self.async_notify(*notice)

    async def _async_flux2_decide(self, source: str) -> tuple[str, str] | None:
        """Decide and push under the Flux 2 lock; returns the notification to send after it."""
        if self._outage_active():
            return  # Phase 48: a push cannot reach the inverter during an outage.
        if self.operation_mode == "monitor":
            self.coordinator.update_state(
                operation_mode="monitor",
                last_flux2_action={"action": "monitor_only", "notified": False, "source": source},
            )
            return

        held_by = self._slots_held_by()
        if held_by:
            self.coordinator.update_state(
                operation_mode=self.operation_mode,
                last_flux2_action={"action": f"paused_{held_by}", "notified": False, "source": source},
            )
            return

        # Pre-flight: both SOC and grid_pac are decision inputs. If either is
        # missing the trim/export logic would fire on garbage data.
        soc = self._essential_state(self.battery_soc_entity)
        grid_pac = self._essential_state(self.grid_pac_entity)
        if soc is None or grid_pac is None:
            missing = [
                entity
                for entity, value in ((self.battery_soc_entity, soc), (self.grid_pac_entity, grid_pac))
                if value is None
            ]
            msg = f"Flux 2 check skipped — unavailable: {', '.join(missing)}"
            _LOGGER.warning(msg)
            self.coordinator.update_state(
                last_error=msg,
                last_flux2_action={
                    "action": "skipped",
                    "notified": False,
                    "source": source,
                    "reason": "essential_entity_unavailable",
                    "missing": missing,
                },
            )
            return

        cfg = self.cfg
        now_local = dt_util.now()
        is_full_day = now_local.strftime("%A") == self.selected_full_charge_day
        base_action = {"soc": soc, "grid_pac": grid_pac, "source": source}

        export_threshold = float(cfg[CONF_EXPORT_DISABLE_THRESHOLD])
        in_peak_window = 16 <= now_local.hour < 19
        watt_trigger = in_peak_window and grid_pac > export_threshold

        # Cost-aware trigger (v1.0.11 Part 3): the same decision, computed from
        # the user's own configured 16:00-19:00 import price instead of a flat
        # Watt number. cost_trigger stays None outside the window or when the
        # charges config has no matching import row — never a false 0p price.
        cost_threshold = float(
            cfg.get(
                CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
                DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
            )
        )
        cost_pence_per_hour: float | None = None
        cost_trigger: bool | None = None
        if in_peak_window:
            prices, sources = self._tariff_prices(now_local.date())
            peak_price = prices["peak"]
            base_action["price_source"] = sources["peak"]
            if peak_price is not None:
                cost_pence_per_hour = (grid_pac / 1000) * peak_price
                cost_trigger = cost_pence_per_hour > cost_threshold
                if cost_trigger != watt_trigger:
                    self._tally_shadow_export_divergence(cost_pence_per_hour, cost_threshold, cost_trigger)

        shadow_mode = bool(cfg.get(CONF_COST_AWARE_EXPORT_SHADOW_MODE, DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE))
        use_cost = not shadow_mode and cost_trigger is not None
        trigger_disable = cost_trigger if use_cost else watt_trigger

        if trigger_disable:
            # Idempotency: SOC changes can fire this check every ~30s for the whole
            # 16:00–19:00 window. If export is already paused, don't re-push the
            # identical payload to the API or re-notify on every tick.
            if self.coordinator.state.evening_export_disabled:
                self.coordinator.update_state(
                    last_flux2_action={
                        "action": "disable_evening_export",
                        **base_action,
                        "notified": False,
                        "reason": "already_disabled",
                        "api_ok": True,
                    },
                    evening_export_disabled=True,
                    operation_mode=self.operation_mode,
                    touch=False,
                )
                return

            if use_cost:
                reason = f"cost_{round(cost_pence_per_hour, 1)}p/h_exceeds_{round(cost_threshold, 1)}p/h"
                trigger_desc = f"Cost is {round(cost_pence_per_hour, 1)}p/h during the 16:00–19:00 export window"
            else:
                reason = f"grid_pac_{round(grid_pac)}W_exceeds_{round(export_threshold)}W"
                trigger_desc = f"Grid draw is {round(grid_pac)} W during the 16:00–19:00 export window"
            api_ok = await self._async_push_flux2_action(
                "disable_evening_export",
                dict(EXPORT_PAUSE_FLUX2_SLOT),
                base_action,
                reason,
                evening_export_disabled=True,
            )
            title = "🔋 Sunsynk: evening export paused" if api_ok else "⚠️ Sunsynk: export pause NOT applied"
            return (
                title,
                f"{trigger_desc}. "
                f"Export paused by holding target SOC at 100%; it re-enables automatically.{_api_note(api_ok)}",
            )

        # Trim if SOC exceeds 85% on a non-full-charge day. Target 82% leaves a 3% gap
        # below the trigger so normal fluctuation doesn't immediately re-trigger a trim.
        # Never below the evening reserve, never at night, never below tonight's charge.
        trim_target, reserve = self._trim_target(soc, now_local)
        if (
            not is_full_day and soc > 85 and trim_target is not None
            and not self._peak_export_holds_flux2(now_local) and self._cooldown_ok()
        ):
            self._mark_trim()
            trim_target = await self._async_trim_defer(soc, trim_target, now_local, cfg, base_action)
            if trim_target is None:
                return
            api_ok = await self._async_push_flux2_action(
                "trim_to_82",
                {
                    "startTime": now_local.strftime("%H:%M"),
                    "endTime": (now_local + timedelta(minutes=45)).strftime("%H:%M"),
                    "targetSoc": trim_target,
                },
                {**base_action, "evening_reserve_soc": reserve,
                 "evening_reserve_soc_learned": self._evening_reserve_pair()[1]},
                f"soc_{round(soc)}%_exceeds_85",
            )
            title = "🔋 Sunsynk: battery trim" if api_ok else "⚠️ Sunsynk: battery trim NOT applied"
            return (
                title,
                f"SOC {round(soc, 1)}% is above 85%. Trimming to {trim_target}%"
                f"{_reserve_note(trim_target, reserve)}.{_api_note(api_ok)}",
            )

        was_paused = bool(self.coordinator.state.evening_export_disabled)
        self.coordinator.update_state(
            last_flux2_action={
                "action": "none",
                **base_action,
                "notified": False,
                "reason": "no_trigger",
            },
            evening_export_disabled=False,
            operation_mode=self.operation_mode,
            # Runs on every SOC change above 85%: only a real change (the pause clearing) is
            # stamped and saved; the live soc/grid_pac in the action still reach the entities.
            touch=was_paused,
            persist=was_paused,
        )
        return None

    async def _async_trim_defer(self, soc, trim_target, now_local, cfg, base_action) -> int | None:
        """Phase 65: size the trim to the solar that would overflow; the rest waits for the peak.

        Always logs a trim_shadow record before 16:00. Live only with trim_defer_live and peak_export_live
        (the deferred kWh is sold by the peak export). Returns the target to push, or None to skip the push.
        """
        minutes = now_local.hour * 60 + now_local.minute
        if minutes >= PEAK_COVER_PEAK_START_MINUTES:
            return trim_target
        hour_now = minutes / 60.0
        hourly = self._hourly_or_synthetic_forecast(cfg, now_local)
        raw = solar_until_kwh(hourly, hour_now)
        scale = intraday_forecast_scale(
            self._essential_state(self.day_pv_energy_entity), solar_until_kwh(hourly, 0.0, hour_now), hour_now
        )
        # Overflow risk: use the higher of raw and scaled solar so a surprise sunny day still trims.
        pv = raw if raw is None or scale is None else max(raw, scaled_solar_kwh(raw, scale))
        clipped = (self._clipped.get("date") == now_local.date().isoformat()) and bool(self._clipped.get("periods"))
        plan = trim_defer_kwh(
            soc, trim_target, self._capacity_kwh(cfg), pv, self._plan_load_kw(cfg),
            (PEAK_COVER_PEAK_START_MINUTES - minutes) / 60.0, clipped,
        )
        prices, _src = self._tariff_prices(now_local.date())
        live = (
            bool(cfg.get(CONF_TRIM_DEFER_LIVE, DEFAULT_TRIM_DEFER_LIVE))
            and bool(cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE))
            and self.operation_mode != "monitor"
        )
        await self.data_logger.async_log_trim_shadow(
            date=now_local.date().isoformat(), time=now_local.strftime("%H:%M"), soc=soc, trim_target=trim_target,
            remaining_pv_kwh=pv, intraday_scale=scale, clipped_today=clipped,
            export_peak_pence=prices["export_peak"], live=live, **plan,
        )
        if not live or plan["defer_kwh"] <= 0:
            return trim_target
        if plan["needed_soc"] >= soc:
            self.coordinator.update_state(
                last_flux2_action={"action": "trim_deferred", **base_action, "notified": False,
                                   "reason": f"defer_{plan['defer_kwh']}kWh_to_peak", "api_ok": True},
                touch=False,
            )
            return None
        return plan["needed_soc"]

    async def _async_push_flux2_action(
        self,
        action: str,
        flux_2: dict[str, Any],
        base_action: dict[str, Any],
        reason: str,
        evening_export_disabled: bool = False,
    ) -> bool:
        """Push a Flux 2 override and record it as last_flux2_action. Returns api_ok."""
        now = dt_util.now()
        payload = carry_held_flux1({"flux_2": flux_2}, self._cover_hold_slot(now), now.hour * 60 + now.minute)
        api_ok = await self.async_push_flux_override(payload)
        self.coordinator.update_state(
            last_flux2_action={
                "action": action,
                **base_action,
                "payload": payload,
                "notified": True,
                "reason": reason,
                "api_ok": api_ok,
            },
            evening_export_disabled=evening_export_disabled,
            operation_mode=self.operation_mode,
        )
        return api_ok

    def _tally_shadow_export_divergence(
        self,
        cost_pence_per_hour: float,
        cost_threshold: float,
        cost_trigger: bool,
    ) -> None:
        """Record one 30-minute check where the cost trigger disagreed with the Watt trigger.

        estimated_gbp_delta is a rough indicator, not a precise financial
        claim: it only accumulates when the cost trigger would pause export
        but the Watt trigger didn't, valuing the half-hour of exposure above
        the cost threshold at the configured peak import price. The reverse
        case (Watt paused, cost trigger says it's fine) carries no cost risk —
        export was already disabled, so nothing is added for it, only the
        check count.
        """
        self._shadow_export_stats["divergent_checks"] += 1
        if cost_trigger:
            excess_pence_per_hour = cost_pence_per_hour - cost_threshold
            self._shadow_export_stats["estimated_gbp_delta"] += round(excess_pence_per_hour * 0.5 / 100, 4)

    async def _async_peak_cover_after_startup(self, _now=None) -> None:
        """Peak cover straight after a restart, retried a minute later while SOC is unreadable or an
        outage is still flagged (phase 75 follow-up: 04/11 lost its 66% hold this way)."""
        await self._guarded(self.async_run_peak_cover, "Peak cover")
        now = dt_util.now()
        if peak_cover_startup_retry_due(
            now.hour * 60 + now.minute, self._essential_state(self.battery_soc_entity),
            self._outage_active(), self._peak_cover_startup_attempts,
        ):
            self._peak_cover_startup_attempts += 1
            _LOGGER.info("Peak cover after restart deferred — SOC unavailable or outage flagged")
            self.unsubs.append(async_call_later(self.hass, 60, self._async_peak_cover_after_startup))

    async def _async_periodic_flux2_check(self, _now) -> None:
        """30-minute interval callback."""
        await self._guarded(self.async_run_flux2_check, "Periodic Flux 2 check")
        await self._guarded(self._async_track_peak_window_usage, "Peak-window usage tracking")
        await self._guarded(self.async_restore_charge_current, "Grid charge current restore")
        await self._guarded(self.async_run_peak_cover, "Peak cover")
        await self._guarded(self._async_track_clipped_solar, "Clipped solar tracking")
        await self._guarded(self._async_track_voltage_derate, "Voltage derate tracking")
        self._sample_hourly()
        await self._guarded(self._async_track_pv_strings, "PV string tracking")
        await self._guarded(self._async_track_over_limit_import, "Over-limit import tracking")
        await self._guarded(self._async_daytime_import_check, "Daytime import check")
        await self._guarded(self._async_refresh_marginal_cost, "Cost of 1 kWh now")
        await self._guarded(self._async_track_outside_temp, "Outside temperature")
        await self._guarded(self.async_run_mpc_shadow, "MPC shadow plan")
        await self._guarded(self.async_run_flex_event_shadow, "Flex event shadow")

    async def _async_track_clipped_solar(self) -> None:
        """Tally solar lost while the battery is full and export is at the limit (phase 29); no writes."""
        limit = float(self.cfg.get(CONF_EXPORT_LIMIT_W) or 0)
        if limit <= 0:
            return
        now = dt_util.now()
        today = now.date().isoformat()
        if self._clipped.get("date") != today:
            self._clipped = {"date": today, "kwh": None, "periods": 0, "first_full_hour": None}
        soc = self._essential_state(self.battery_soc_entity)
        if soc is not None and soc >= CLIPPED_SOC_PCT and self._clipped["first_full_hour"] is None:
            self._clipped["first_full_hour"] = now.hour + now.minute / 60.0
        if not is_clipping(soc, self._essential_state(self.grid_pac_entity), limit):
            return
        hourly = self._get_hourly_forecast_kwh()
        pv_parts = [self._essential_state(e) for e in (self.pv_mppt0_entity, self.pv_mppt1_entity)]
        pv_w = sum(p for p in pv_parts if p is not None) if any(p is not None for p in pv_parts) else None
        lost = estimate_clipped_kwh(hourly.get(now.hour) if hourly else None, pv_w)
        self._clipped["periods"] += 1
        if lost is not None:
            self._clipped["kwh"] = (self._clipped["kwh"] or 0.0) + lost

    async def _async_track_voltage_derate(self) -> None:
        """30-min: tally minutes where high grid voltage looks to be curtailing solar (phase 83); no writes.

        Logged as ``export_derated_min`` on day_actuals so the feasibility scan can count affected
        days. Missing voltage/PV/load reads are never counted. A restart loses the day's tally."""
        today = dt_util.now().date().isoformat()
        if self._derated.get("date") != today:
            self._derated = {"date": today, "minutes": 0, "seen": False}
        pv_parts = [self._essential_state(e) for e in (self.pv_mppt0_entity, self.pv_mppt1_entity)]
        pv_w = sum(p for p in pv_parts if p is not None) if any(p is not None for p in pv_parts) else None
        voltage = self._essential_state(self.grid_voltage_entity)
        self._derated["seen"] = self._derated["seen"] or voltage is not None
        if voltage_derate_suspect(
            voltage, self._essential_state(self.grid_pac_entity), pv_w,
            self._essential_state(self.load_power_entity), self._essential_state(self.battery_soc_entity),
        ):
            self._derated["minutes"] += VOLTAGE_DERATE_PERIOD_MIN

    async def _async_track_over_limit_import(self) -> None:
        """Tally import the battery could not cover (load above the inverter's output); no writes.

        Sampled on every SOC change, the 30-min tick and each meter snapshot.
        Transient like `_clipped`: a restart loses the day's tally so far, which
        only leaves that import counted as avoidable. Skipped while an event
        holds the slots (its import is credited separately).
        """
        now = dt_util.now()
        today = now.date().isoformat()
        if self._over_limit.get("date") != today:
            self._over_limit = {"date": today, "prev": None, "kwh": {}}
        sample = {
            "minute": now.hour * 60 + now.minute + now.second / 60.0,
            "soc": self._essential_state(self.battery_soc_entity),
            "grid_import_kwh": self._essential_state(self.day_grid_import_entity),
        }
        if not self._slots_held():
            segment, kwh = over_limit_import_step(self._over_limit["prev"], sample)
            if segment:
                self._over_limit["kwh"][segment] = self._over_limit["kwh"].get(segment, 0.0) + kwh
        self._over_limit["prev"] = sample
        self._track_import_cause(now, today, sample)

    def _track_import_cause(self, now, today: str, sample: dict[str, Any]) -> None:
        """Phase 42: assign import since the last sample to a cause (no writes); transient like _over_limit."""
        if self._import_causes.get("date") != today:
            self._import_causes = {"date": today, "prev": None, "kwh": {}}
        cover = self._peak_cover or {}
        sample = {
            **sample,
            "slots_held": bool(self._slots_held()),
            "slot_hold": bool(cover.get("pushed") and cover.get("date") == today),
        }
        cause, kwh = import_cause_step(self._import_causes["prev"], sample)
        if cause:
            self._import_causes["kwh"][cause] = self._import_causes["kwh"].get(cause, 0.0) + kwh
        self._import_causes["prev"] = sample
        totals = {k: round(v, 2) for k, v in self._import_causes["kwh"].items()}
        shown = {"date": today, **totals, "total_kwh": round(sum(totals.values()), 2)}
        if shown != self.coordinator.state.import_causes:
            self.coordinator.update_state(touch=False, persist=False, import_causes=shown)

    async def _async_daytime_import_check(self) -> None:
        """05:00-16:00: alert once a day on sustained grid import while the battery should cover the house (phase 40).

        Read-only, so it runs in monitor mode too. Skipped while an event or
        peak cover holds the slots (import is intended then).
        """
        if not self.cfg.get(CONF_BASELOAD_ALERT, DEFAULT_BASELOAD_ALERT):
            return
        now = dt_util.now()
        today = now.date().isoformat()
        if self._daytime_import.get("date") != today:
            self._daytime_import = {"date": today, "samples": [], "alerted": False}
        state = self._daytime_import
        if not DAYTIME_IMPORT_FROM_HOUR <= now.hour < DAYTIME_IMPORT_UNTIL_HOUR or state["alerted"]:
            return
        if self._slots_held() or (self._peak_cover or {}).get("pushed"):
            state["samples"] = []
            return
        state["samples"] = (state["samples"] + [self._essential_state(self.grid_pac_entity)])[-DAYTIME_IMPORT_CHECKS:]
        away = bool(self.coordinator.state.away_mode)
        threshold = DAYTIME_IMPORT_AWAY_THRESHOLD_W if away else DAYTIME_IMPORT_THRESHOLD_W
        anomaly = daytime_import_anomaly(
            state["samples"], self._essential_state(self.battery_soc_entity), threshold
        )
        if anomaly is None:
            return
        state["alerted"] = True
        await self.async_notify(
            "🔋 Sunsynk: daytime grid import",
            f"Importing {anomaly['grid_w']} W from the grid for the last {anomaly['checks']} checks "
            f"with the battery at {anomaly['soc']}% — inverter limit, wrong priority or a heater left on?",
        )

    async def _async_track_peak_window_usage(self) -> None:
        """Snapshot load/grid meters at the 16:00-19:00 window edges and log the delta.

        Piggybacks on the 30-minute periodic callback rather than adding a new
        scheduled listener — there's no existing capture point at exactly 16:00.
        Captures a start snapshot the first time this fires with the hour in
        [16, 19), then logs the delta the first time it fires with hour >= 19
        (guarded so it only logs once per day). If HA restarts mid-window,
        _peak_window_start is lost (not persisted) and that day is silently
        skipped rather than logging a wrong/partial delta.
        """
        now_local = dt_util.now()
        today = now_local.date().isoformat()
        hour = now_local.hour

        if 16 <= hour < 19:
            if self._peak_window_start is None or self._peak_window_start.get("date") != today:
                self._peak_window_start = {"date": today, **self._read_daily_meters()}
            return

        if hour >= 19 and self._peak_window_start is not None and self._peak_window_start.get("date") == today:
            start = self._peak_window_start
            self._peak_window_start = None
            end = self._read_daily_meters()
            if any(v is None for v in (*start.values(), *end.values())):
                return  # an unavailable meter would log a whole-day or negative delta
            peak_load_kwh = max(0.0, end["load"] - start["load"])
            peak_grid_import_kwh = max(0.0, end["grid_import"] - start["grid_import"])
            peak_grid_export_kwh = max(0.0, end["grid_export"] - start["grid_export"])
            await self.data_logger.async_log_peak_window_usage(
                date=today,
                peak_load_kwh=peak_load_kwh,
                peak_grid_import_kwh=peak_grid_import_kwh,
                peak_grid_export_kwh=peak_grid_export_kwh,
            )
            self.coordinator.update_state(
                touch=False,
                last_peak_window_usage={
                    "type": "peak_window_usage",
                    "date": today,
                    "peak_load_kwh": round(peak_load_kwh, 2),
                    "peak_grid_import_kwh": round(peak_grid_import_kwh, 2),
                    "peak_grid_export_kwh": round(peak_grid_export_kwh, 2),
                },
            )

    async def _async_peak_export(self, _now) -> None:
        await self._guarded(self.async_run_peak_export, "Peak surplus export")

    async def async_run_peak_export(self) -> None:
        """16:00: sell the SOC above the evening reserve + margin at the peak rate (phase 17).

        Shadow by default: the decision, kWh and £ are logged and nothing is
        pushed. Live (CONF_PEAK_EXPORT_LIVE) pushes Flux 2 16:00-19:00 to the
        target via _async_post_with_status. Skipped while a free event
        holds the slots; never pushes in monitor mode; the export-disable (watt
        trigger) wins — if it already fired, or fires later, it holds 100%.
        """
        if self._outage_active():
            return  # Phase 48
        now = dt_util.now()
        slots_held = self._slots_held()
        if not slots_held:
            await self._async_peak_cover_end()
        soc = self._essential_state(self.battery_soc_entity)
        skip = peak_export_skip_reason(slots_held, soc)
        if skip:
            # Logged so the 22:00 debug bundle always says why there was no export.
            record = {"type": "peak_export", "date": now.date().isoformat(), "soc": soc, "decision": skip}
            self.coordinator.update_state(touch=False, peak_export=record)
            await self.data_logger.async_log_peak_export(**{k: v for k, v in record.items() if k != "type"})
            return
        cfg = self.cfg
        reserve = self._evening_reserve_soc()
        capacity = self._capacity_kwh(cfg)
        # Keep the 16:00-19:00 house load too, sized like peak cover, so the peak isn't bought back.
        peak_load_kwh, peak_load_source = peak_load_estimate_kwh(
            await self.data_logger.async_recent_peak_loads(), 3.0 * self._plan_load_kw(cfg)
        )
        need = peak_need_soc(peak_load_kwh, capacity)
        prices, sources = self._tariff_prices(now.date())
        plan = peak_export_plan(
            soc,
            reserve,
            capacity,
            prices["export_peak"],
            prices["offpeak"],
            wear_pence=self._wear_pence(),
            peak_need_soc=need,
        )
        live = bool(cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE)) and self.operation_mode != "monitor"
        api_ok = None
        async with self._flux2_lock:  # re-read the pause flag once any in-flight pause has finished
            if plan["decision"] == "export" and self.coordinator.state.evening_export_disabled:
                plan["decision"] = "export_disabled"
            if live and plan["decision"] == "export":
                api_ok = await self._async_push_flux2_action(
                    "peak_export",
                    {"startTime": "16:00", "endTime": "19:00", "targetSoc": plan["target_soc"]},
                    {"soc": soc, "source": "automatic", "evening_reserve_soc": reserve},
                    f"surplus_{plan['export_kwh']}kWh_above_{plan['target_soc']}%",
                )
        if api_ok is not None:
            title = "🔋 Sunsynk: selling surplus at peak rate" if api_ok else "⚠️ Sunsynk: peak export NOT applied"
            await self.async_notify(
                title,
                f"SOC {round(soc, 1)}%: exporting about {plan['export_kwh']} kWh down to "
                f"{plan['target_soc']}% (evening reserve {reserve}% + peak use) until 19:00.{_api_note(api_ok)}",
            )
        record = {
            "type": "peak_export",
            "date": now.date().isoformat(),
            "soc": soc,
            "evening_reserve_soc": reserve,
            "peak_load_kwh": peak_load_kwh,
            "peak_load_source": peak_load_source,
            "peak_need_soc": round(need, 1),
            **plan,
            "price_source": price_source(sources, "export_peak", "offpeak"),
            "live": live,
            "api_ok": api_ok,
        }
        self.coordinator.update_state(touch=False, peak_export=record)
        await self.data_logger.async_log_peak_export(**{k: v for k, v in record.items() if k != "type"})
        await self._async_log_multi_day_shadow("peak_export", peak_target_soc=plan["target_soc"], reserve=reserve)

    async def _async_peak_export_recheck(self, _now) -> None:
        await self._guarded(self.async_run_peak_export_recheck, "Peak export re-check")

    async def async_run_peak_export_recheck(self) -> None:
        """17:00 and 18:00: re-size today's peak export from the load seen since 16:00 (phase 66).

        Builds on the 16:00 decision (the stored peak_export record). Always logs
        a peak_export_recheck record; pushes Flux 2 now->19:00 only when live
        (CONF_PEAK_EXPORT_LIVE), not in monitor mode, no free event holds the
        slots and the export-disable has not fired.
        """
        now = dt_util.now()
        today = now.date().isoformat()
        prior = record_for_date(self.coordinator.state.peak_export, today)
        if self._slots_held() or self.coordinator.state.evening_export_disabled or prior.get("target_soc") is None:
            return
        soc = self._essential_state(self.battery_soc_entity)
        snapshots = await self.data_logger.async_load_meter_snapshots(today)
        load_16 = (snapshots.get("16:00") or {}).get("load_kwh")
        load_now = self._essential_state(self.day_load_entity)
        observed = round(load_now - load_16, 3) if load_now is not None and load_16 is not None else None
        elapsed = now.hour * 60 + now.minute - 16 * 60
        cfg = self.cfg
        reserve = self._evening_reserve_soc()
        prices, sources = self._tariff_prices(now.date())
        result = peak_export_recheck(
            soc, reserve, self._capacity_kwh(cfg), elapsed, 19 * 60 - (16 * 60 + elapsed), observed,
            prior.get("target_soc"), prior.get("api_ok") is True,
            prices["export_peak"], prices["offpeak"], wear_pence=self._wear_pence(),
        )
        live = bool(cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE)) and self.operation_mode != "monitor"
        api_ok = None
        async with self._flux2_lock:  # a pause that fired meanwhile wins
            if live and result["action"] and not self.coordinator.state.evening_export_disabled:
                api_ok = await self._async_push_flux2_action(
                    "peak_export",
                    {"startTime": now.strftime("%H:%M"), "endTime": "19:00", "targetSoc": result["target_soc"]},
                    {"soc": soc, "source": "automatic", "evening_reserve_soc": reserve},
                    f"recheck_{result['action']}_to_{result['target_soc']}%",
                )
        if api_ok is not None:
            title = "🔋 Sunsynk: peak export adjusted" if api_ok else "⚠️ Sunsynk: peak export adjustment NOT applied"
            await self.async_notify(
                title,
                f"Load since 16:00 is {observed} kWh, so the peak export target moves from "
                f"{prior.get('target_soc')}% to {result['target_soc']}% until 19:00.{_api_note(api_ok)}",
            )
            if api_ok:
                # Keep the 16:00 record in step so the window stays owned until 19:00 and reports match.
                self.coordinator.update_state(
                    touch=False,
                    peak_export={**prior, "api_ok": True, "target_soc": result["target_soc"], "rechecked_at": now.strftime("%H:%M")},
                )
        record = {
            "date": today,
            "checked_at": now.strftime("%H:%M"),
            "soc": soc,
            "evening_reserve_soc": reserve,
            **result,
            "price_source": price_source(sources, "export_peak", "offpeak"),
            "live": live,
            "api_ok": api_ok,
        }
        await self.data_logger.async_log_peak_export_recheck(**record)

    def _outage_active(self) -> bool:
        """True while a grid outage is open (phase 48): plan, trim, export and retry pushes are held."""
        return bool((self.coordinator.state.grid_outage or {}).get("active"))

    def _refresh_battery_runtime(self) -> None:
        """Battery runtime left at the live load (phase 48); unknown when SOC or load is unavailable."""
        soc = self._essential_state(self.battery_soc_entity)
        load_w = self._essential_state(self.load_power_entity)
        hours = outage_runtime_hours(
            soc, BATTERY_BOTTOM_SOC, self._capacity_kwh(), None if load_w is None else load_w / 1000
        )
        self.coordinator.update_state(
            touch=False, persist=False,
            battery_runtime={"hours": hours, "soc": soc, "load_kw": None if load_w is None else round(load_w / 1000, 2)},
        )

    async def _async_grid_status_changed(self, event: Event) -> None:
        """Voltage event → outage open/close."""
        new_state = event.data.get("new_state")
        await self._guarded(
            lambda: self._async_apply_grid_online(grid_online_from_voltage(None if new_state is None else new_state.state)),
            "Grid status change",
        )

    async def _async_grid_reconcile(self) -> None:
        """At startup, close a persisted outage that the live voltage says is over (or never was)."""
        await self._guarded(
            lambda: self._async_apply_grid_online(grid_online_from_voltage(getattr(self._available_state(self.grid_voltage_entity), "state", None))),
            "Grid reconcile",
        )

    async def _async_apply_grid_online(self, online: bool | None) -> None:
        if online is None:
            return
        state = self.coordinator.state
        now_iso = dt_util.now().isoformat()
        soc = self._essential_state(self.battery_soc_entity)
        if online is False and not self._outage_active():
            self.coordinator.update_state(
                grid_outage={"active": True, "start": now_iso, "soc_start": soc, "soc_low": soc}
            )
            self._refresh_battery_runtime()
            runtime = (state.battery_runtime or {}).get("hours")
            await self.async_notify(
                "🔋 Sunsynk: grid outage",
                f"Grid power lost. Battery {'' if soc is None else f'{soc:.0f}% '}"
                + (f"should last about {runtime:g} h at the current load." if runtime is not None else "runtime unknown.")
                + " Plan and export pushes are paused until it returns.",
            )
        elif online is True and self._outage_active():
            outage = outage_track(dict(state.grid_outage), soc)
            record = outage_record(outage, now_iso, soc, self._capacity_kwh())
            await self.data_logger.async_log_outage(**record)
            skipped = bool(state.grid_outage.get("plan_skipped"))
            self.coordinator.update_state(grid_outage={"active": False, "last": record})
            self._refresh_battery_runtime()
            minutes = record.get("minutes")
            await self.async_notify(
                "🔋 Sunsynk: grid restored",
                "Grid power is back" + (f" after {minutes} min" if minutes is not None else "")
                + (f"; the battery went down to {record['soc_low']:.0f}%." if record.get("soc_low") is not None else ".")
                + " Normal planning resumes.",
            )
            if skipped:
                await self._guarded(lambda: self.async_run_import_plan(source="grid_restored"), "Post-outage plan")

    def _peak_export_holds_flux2(self, now_local) -> bool:
        """True while today's live peak export window owns Flux 2 (16:00-19:00)."""
        rec = record_for_date(self.coordinator.state.peak_export, now_local.date().isoformat())
        return (
            rec.get("api_ok") is True
            and 16 <= now_local.hour < 19
        )

    def _cover_hold_slot(self, now_local: datetime) -> dict[str, Any] | None:
        """Today's live peak-cover Flux 1 hold while it is in force (pushed, before 16:00), else None."""
        state = self._peak_cover or {}
        if not state.get("pushed") or state.get("date") != now_local.date().isoformat():
            return None
        if now_local.hour * 60 + now_local.minute >= PEAK_COVER_PEAK_START_MINUTES:
            return None
        return state.get("slot")

    async def _async_battery_soc_changed(self, event: Event) -> None:
        await self._guarded(lambda: self._async_do_battery_soc_changed(event), "SOC change")

    async def _async_do_battery_soc_changed(self, event: Event) -> None:
        """Handle SOC threshold-based reactions."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        self._read_soc()
        self._refresh_battery_runtime()
        if self._outage_active():
            try:
                self.coordinator.update_state(
                    touch=False, grid_outage=outage_track(dict(self.coordinator.state.grid_outage), float(new_state.state))
                )
            except (ValueError, TypeError):
                pass
            return
        await self._guarded(self._async_refresh_marginal_cost, "Cost of 1 kWh now")
        await self._guarded(self._async_track_over_limit_import, "Over-limit import tracking")
        if self._slots_held():
            return

        try:
            soc = float(new_state.state)
        except (ValueError, TypeError):
            return

        is_full_day = dt_util.now().strftime("%A") == self.selected_full_charge_day
        if soc >= 99.5 and is_full_day:
            if self.pending_full_trim_cancel:
                return

            async def _delayed_full_trim(_later) -> None:
                await self._guarded(_do_full_trim, "Full-charge trim")

            async def _do_full_trim() -> None:
                self.pending_full_trim_cancel = None
                current_soc = self._state_float(self.battery_soc_entity, 0)
                if current_soc < 99.5:
                    return
                async with self._flux2_lock:
                    now_local = dt_util.now()
                    trim_target, reserve = self._trim_target(current_soc, now_local)
                    # Don't replace a live peak export (it already sells lower), an export
                    # pause or an event's slots.
                    if (
                        trim_target is None
                        or self._peak_export_holds_flux2(now_local)
                        or self.coordinator.state.evening_export_disabled
                        or self._slots_held()
                    ):
                        return
                    api_ok = await self._async_push_flux2_action(
                        "full_day_trim_to_82",
                        {
                            "startTime": now_local.strftime("%H:%M"),
                            "endTime": (now_local + timedelta(minutes=60)).strftime("%H:%M"),
                            "targetSoc": trim_target,
                        },
                        {
                            "soc": current_soc,
                            "grid_pac": self._state_float(self.grid_pac_entity, 0),
                            "source": "automatic",
                            "evening_reserve_soc": reserve,
                        },
                        "held_100%_for_1h",
                    )
                title = "🔋 Sunsynk: full-charge hold complete" if api_ok else "⚠️ Sunsynk: full-charge trim NOT applied"
                await self.async_notify(
                    title,
                    f"Held at 100% for 1 hour. Trimming to {trim_target}%"
                    f"{_reserve_note(trim_target, reserve)}.{_api_note(api_ok)}",
                )

            # Hold at 100% for 1 hour to fully condition the cells, then trim to 82%.
            self.pending_full_trim_cancel = async_call_later(
                self.hass,
                3600,
                _delayed_full_trim,
            )
            self.coordinator.update_state(
                last_flux2_action={
                    "action": "schedule_full_trim",
                    "soc": soc,
                    "notified": False,
                    "source": "automatic",
                    "reason": "soc_reached_99.5%_on_full_day",
                },
                operation_mode=self.operation_mode,
            )

        elif soc > 85 and not is_full_day:
            await self.async_run_flux2_check()

    async def _async_refresh_marginal_cost(self) -> None:
        """Recompute sensor.cost_of_1_kwh_now (phase 38); read-only, writes state only when it changes."""
        cfg = self.cfg
        now = dt_util.now()
        minutes = now.hour * 60 + now.minute
        prices, _sources = self._tariff_prices(now.date())
        export_bands = flux_bands_from_charges(cfg.get(CONF_CHARGES, []), "export")
        soc = self._essential_state(self.battery_soc_entity)
        # Battery must still cover the house from now (or 16:00) to 02:00.
        start = minutes - 24 * 60 if minutes < 2 * 60 else max(minutes, 16 * 60)
        hours = max(0.0, (26 * 60 - start) / 60.0)
        reserve = evening_reserve_soc(self._plan_load_kw(cfg), self._capacity_kwh(cfg), hours=hours)
        pv_parts = [self._essential_state(e) for e in (self.pv_mppt0_entity, self.pv_mppt1_entity)]
        pv_w = sum(p for p in pv_parts if p is not None) if any(p is not None for p in pv_parts) else None
        spare = solar_is_spare(self._essential_state(self.grid_pac_entity), pv_w)
        result = marginal_cost_pence(minutes, soc, reserve, spare, prices, export_bands, self._wear_pence())
        spare_solar = self.coordinator.state.spare_solar or {}
        spare_hours = spare_solar.get("hours") if spare_solar.get("date") == now.date().isoformat() else None
        result["cheapest_hour_today"] = cheapest_hour_today(now.hour, prices, export_bands, spare_hours)
        result["reserve_soc"] = reserve
        if result != self.coordinator.state.marginal_cost:
            self.coordinator.update_state(touch=False, persist=False, marginal_cost=result)

    def _refresh_spare_solar(self) -> dict[str, Any] | None:
        """Recompute the spare-solar estimate into state (no inverter writes); None without a forecast."""
        cfg = self.cfg
        now = dt_util.now()
        soc = self._essential_state(self.battery_soc_entity)
        capacity = self._capacity_kwh(cfg)
        hourly = self._hourly_or_synthetic_forecast(cfg, now)
        plan = self.coordinator.state.last_import_plan or {}
        correction = float(plan.get("forecast_correction_factor") or 1.0)
        # Phase 56: from 10:00 rescale the rest of the day by PV produced so far (advisory only).
        hour_now = now.hour + now.minute / 60.0
        scale = intraday_forecast_scale(
            self._essential_state(self.day_pv_energy_entity), solar_until_kwh(hourly, 0.0, hour_now), hour_now
        )
        if scale is not None:
            correction *= scale
        load_kw = self._plan_load_kw(cfg)
        headroom = capacity * (100.0 - soc) / 100.0 if soc is not None else 0.0
        result = expected_spare_solar(
            hourly, correction, load_kw, headroom, from_hour=max(SPARE_SOLAR_FIRST_HOUR, now.hour + now.minute / 60.0)
        )
        result["date"] = now.date().isoformat()
        self.coordinator.update_state(spare_solar=result)
        return result

    async def _async_spare_solar_startup(self, _now) -> None:
        """90 s after startup: first spare-solar estimate. A coroutine so HA runs it on the event loop."""
        await self._guarded(self._async_do_spare_solar, "Spare solar")

    async def _async_do_spare_solar(self) -> None:
        self._refresh_spare_solar()

    async def _async_spare_solar_morning(self, _now) -> None:
        await self._guarded(self._async_do_spare_solar_morning, "Spare solar")

    async def _async_do_spare_solar_morning(self) -> None:
        """06:05: refresh the estimate and, if the option is set, tell the user when to run appliances."""
        result = self._refresh_spare_solar()
        raw = self.cfg.get(CONF_SPARE_SOLAR_NOTIFY_KWH)
        try:
            threshold = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            threshold = None
        window = (result or {}).get("window")
        if threshold is None or not window or (result.get("total_kwh") or 0) < threshold:
            return
        await self.async_notify(
            "🔋 Sunsynk: spare solar today",
            f"Spare solar {window['start']}–{window['end']} (~{window['kwh']} kWh) — good time for the washing, "
            f"dishwasher or EV.",
        )

    async def async_run_peak_cover(self) -> None:
        """12:00-16:00, every 30 minutes: will the battery last the 16:00-19:00 peak window?

        Projects SOC at 16:00 and, when it falls short, tops up at the day rate
        with a Flux 1 slot now->16:00 (the inverter only imports below the
        target, so it holds the battery there). Shadow by default
        (CONF_PEAK_COVER_LIVE); never pushes in monitor mode or while an event
        holds the slots. One decision is logged a day: the first actionable
        top-up, else the last check before 16:00.
        """
        now = dt_util.now()
        minutes = now.hour * 60 + now.minute
        if not PEAK_COVER_HOLD_FROM_MINUTES <= minutes < PEAK_COVER_PEAK_START_MINUTES or self._slots_held() or self._outage_active():
            return
        today = now.date().isoformat()
        if (self._peak_cover or {}).get("date") != today:
            self._peak_cover = {"date": today, "logged": False, "pushed": False}
        state = self._peak_cover
        if state["logged"]:
            return
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            return
        cfg = self.cfg
        capacity = self._capacity_kwh(cfg)
        charge_rate_kw = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        fallback_kw = self._plan_load_kw(cfg)
        snapshots = await self.data_logger.async_load_meter_snapshots(today)
        load_at_05 = (snapshots.get("05:00") or {}).get("load_kwh")
        load_now = self._essential_state(self.day_load_entity)
        load_kw, load_source = daytime_load_kw(load_now, load_at_05, now.hour + now.minute / 60.0 - 5.0, fallback_kw)
        peak_load_kwh, peak_load_source, peak_margin = peak_load_estimate_split(
            await self.data_logger.async_recent_peak_load_days(), 3.0 * load_kw, now.weekday() >= 5
        )
        profile_peak_kwh = (self._load_profile or {}).get("peak_kwh")
        if self._profile_live() is not None and profile_peak_kwh is not None:  # phase 71 live
            peak_load_kwh, peak_load_source, peak_margin = profile_peak_kwh, "load_profile", 1.0
        # Pessimistic solar, like the low-solar decisions: the raw (uncorrected) forecast.
        hourly = self._hourly_or_synthetic_forecast(cfg, now)
        solar_kwh = solar_until_kwh(hourly, now.hour + now.minute / 60.0)
        prices, sources = self._tariff_prices(now.date())
        # Phase 56 (shadow): PV produced so far vs the forecast for the same hours. Only the
        # logged what-if uses it; the decision keeps the raw figure (min(raw, scaled) would be the
        # low-solar-safe choice if it goes live).
        hour_now = now.hour + now.minute / 60.0
        intraday_scale = intraday_forecast_scale(
            self._essential_state(self.day_pv_energy_entity), solar_until_kwh(hourly, 0.0, hour_now), hour_now
        )
        scaled_kwh = scaled_solar_kwh(solar_kwh, intraday_scale)
        if solar_kwh is None:
            solar_kwh = 0.0  # no forecast: assume none rather than guess
        plan = peak_cover_plan(
            soc, capacity, PEAK_COVER_PEAK_START_MINUTES - minutes, solar_kwh, load_kw, peak_load_kwh,
            charge_rate_kw, prices["day"], prices["peak"], margin=peak_margin, wear_pence=self._wear_pence(),
        )
        actionable = plan["decision"] in ("top_up", "hold") and plan["push_now"]
        if minutes < PEAK_COVER_FROM_MINUTES and plan["decision"] != "hold":
            return  # 09:00-12:00 is for catching a falling SOC with a hold only
        if not actionable and minutes < PEAK_COVER_LAST_LOG_MINUTES:
            return
        live = bool(cfg.get(CONF_PEAK_COVER_LIVE, DEFAULT_PEAK_COVER_LIVE)) and self.operation_mode != "monitor"
        api_ok = None
        if live and actionable:
            slot = {"startTime": now.strftime("%H:%M"), "endTime": "16:00", "targetSoc": plan["target_soc"]}
            api_ok = await self.async_push_flux_override({"flux_1": slot})
            state["pushed"] = api_ok is True
            state["slot"] = slot
            hold = plan["decision"] == "hold"
            if hold:
                title = "🔋 Sunsynk: holding the battery for the peak" if api_ok else "⚠️ Sunsynk: peak hold NOT applied"
            else:
                title = "🔋 Sunsynk: topping up before the peak" if api_ok else "⚠️ Sunsynk: peak top-up NOT applied"
            await self.async_notify(
                title,
                f"SOC {round(soc)}% would reach about {round(plan['projected_soc_16'])}% by 16:00 but the peak window "
                f"needs {plan['required_soc']}%. Holding {plan['target_soc']}% at the day rate until 16:00 "
                f"(about {plan['topup_kwh']} kWh{' kept in the battery' if hold else ''}).{_api_note(api_ok)}",
            )
        state["logged"] = True
        record = {
            "date": today,
            "checked_at": now.strftime("%H:%M"),
            "soc": soc,
            **plan,
            "solar_kwh_to_16": solar_kwh,
            "intraday_scale": intraday_scale,
            "solar_kwh_to_16_scaled": scaled_kwh,
            "intraday_decision": self._intraday_cover_decision(
                scaled_kwh, solar_kwh, soc, capacity, minutes, load_kw, peak_load_kwh, charge_rate_kw, prices, peak_margin
            ),
            "load_kw": load_kw,
            "load_source": load_source,
            "peak_load_kwh": peak_load_kwh,
            "peak_load_source": peak_load_source,
            "peak_load_kwh_profile": profile_peak_kwh,
            "peak_margin": peak_margin,
            "wear_cost_p": self._wear_pence(),
            "day_pence": prices["day"],
            "peak_pence": prices["peak"],
            "price_source": price_source(sources, "day", "peak"),
            "live": live,
            "api_ok": api_ok,
        }
        state["record"] = record
        await self.data_logger.async_log_peak_cover(**record)

    def _intraday_cover_decision(
        self, scaled_kwh, raw_kwh, soc, capacity, minutes, load_kw, peak_load_kwh, charge_rate_kw, prices, margin
    ) -> str | None:
        """What peak cover would decide on min(raw, scaled) solar (phase 56 shadow); None without a scale."""
        if scaled_kwh is None or raw_kwh is None or scaled_kwh == raw_kwh:
            return None
        return peak_cover_plan(
            soc, capacity, PEAK_COVER_PEAK_START_MINUTES - minutes, min(raw_kwh, scaled_kwh), load_kw, peak_load_kwh,
            charge_rate_kw, prices["day"], prices["peak"], margin=margin, wear_pence=self._wear_pence(),
        )["decision"]

    async def _async_peak_cover_end(self) -> None:
        """16:00: put the normal Flux plan back after a live peak-cover top-up slot."""
        state = self._peak_cover or {}
        if not state.get("pushed") or state.get("date") != dt_util.now().date().isoformat():
            return
        state["pushed"] = False
        await self._async_restore_normal_plan()
