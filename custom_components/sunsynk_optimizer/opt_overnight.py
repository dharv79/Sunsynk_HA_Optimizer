# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: 01:55 import plan, retry, charge watchdog, gentle charging, charge outcome, full-charge day."""
from __future__ import annotations

import json
import logging
from datetime import (
    datetime,
    timedelta,
)
from functools import partial
from typing import Any
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from .const import (
    INTEGRATION_VERSION,
    CONF_CHARGE_RATE,
    CONF_IMPORT_FEEDBACK_LIVE,
    CONF_BATTERY_COST_GBP,
    CONF_BATTERY_LIFETIME_KWH,
    CONF_FULL_WINDOW_LIVE,
    CONF_EVENING_LOAD_LIVE,
    CONF_MEASURED_RATE_LIVE,
    CONF_GENTLE_CHARGE_LIVE,
    CONF_FULL_CHARGE_SKIP_LIVE,
    CONF_BASELOAD_ALERT,
    DEFAULT_BASELOAD_ALERT,
    CONF_BATTERY_VOLTAGE,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_SOLAR_START_OFFSET_HOURS,
    CONF_HOURLY_FORECAST_SENSOR,
    CONF_HOURLY_FORECAST_ATTRIBUTE,
    CONF_WEATHER_ENTITY,
    DEFAULT_WEATHER_ENTITY,
    CONF_TOMORROW_FORECAST_SENSOR,
    DEFAULT_CHARGE_RATE,
    DEFAULT_IMPORT_FEEDBACK_LIVE,
    DEFAULT_FULL_WINDOW_LIVE,
    DEFAULT_EVENING_LOAD_LIVE,
    DEFAULT_MEASURED_RATE_LIVE,
    DEFAULT_GENTLE_CHARGE_LIVE,
    DEFAULT_FULL_CHARGE_SKIP_LIVE,
    DEFAULT_HOURLY_FORECAST_ATTRIBUTE,
    DEFAULT_SOLAR_START_OFFSET_HOURS,
    DEFAULT_TOMORROW_FORECAST_SENSOR,
    FULL_CHARGE_DAY_OPTIONS,
    GENTLE_CHARGE_SETTING,
)
from .planning import (
    apply_user_override,
    achievable_max_soc,
    charge_outcome,
    baseload_anomaly,
    FORECAST_QUANTILE_HISTORY_DAYS,
    FLOOR_CANDIDATE_SOC,
    battery_floor_soc,
    cloud_bucket,
    floor_saving_gbp,
    forecast_quantiles,
    forecast_ratio_samples,
    load_profile,
    CAPACITY_HISTORY_DAYS,
    capacity_estimate,
    capacity_low,
    capacity_samples,
    CHEAP_WINDOW_END,
    CHEAP_WINDOW_HOURS,
    DEFAULT_BATTERY_VOLTAGE,
    LOW_SOLAR_THRESHOLD_KWH,
    STARTUP_PLAN_SOURCE,
    CHARGE_WATCHDOG_RECHECK_MINUTES,
    PLAN_RETRY_INTERVAL_MINUTES,
    plan_retry_due,
    soc_retry_due,
    SOC_RETRY_INTERVAL_MINUTES,
    apply_soc_adjustments,
    charge_efficiency,
    charge_progress_ok,
    evening_reserve_choice,
    flux1_end_minutes,
    forecast_band,
    full_charge_day_move,
    gentle_charge_current_a,
    gentle_current_lowers,
    hhmm_to_minutes,
    import_feedback_adjustment,
    learned_evening_load_kw,
    learned_load_kw,
    max_charge_current_a,
    minutes_to_hhmm,
    explain_plan,
    wear_cost_pence_per_kwh,
    elapsed_seconds,
    free_event_target_cap,
    resolve_load_kw,
    resolve_used_charge_rate,
    round_or_none,
    score_full_charge_day,
    select_target_soc,
    should_log_import_plan,
    solar_kwh_to_fill,
    synthetic_hourly_profile,
    days_since_full,
    full_charge_due,
    trim_target_soc,
    DEFAULT_FLUX2_SLOT,
    weighted_forecast_correction,
    window_grid_kwh,
)
from .opt_common import (
    _watchdog_rate_kw,
)

_LOGGER = logging.getLogger(__name__)

class OvernightMixin:
    """01:55 import plan, retry, charge watchdog, gentle charging, charge outcome, full-charge day (methods of SunsynkOptimizer)."""

    async def async_choose_best_full_charge_day(self) -> None:
        """Choose the best Monday-Friday full-charge day from weather forecast."""
        if self.operation_mode == "monitor":
            self.coordinator.update_state(
                last_flux2_action={"action": "monitor_only", "notified": False},
                operation_mode="monitor",
            )
            return

        # A new weekly pick re-arms the once-a-week daily re-check.
        self.coordinator.update_state(touch=False, full_charge_day_moved_to=None)
        weather_scores = await self._async_weekday_weather_scores()
        if weather_scores is None:
            return
        scores: dict[str, float] = {
            day: (-999.0 if score is None else score) for day, score in weather_scores.items()
        }

        best_day = max(scores, key=scores.get)
        self.coordinator.update_state(
            selected_full_charge_day=best_day,
            last_full_charge_scores=scores,
        )

        await self.data_logger.async_log_full_charge_scores(scores, best_day)

        await self.async_notify(
            "🔋 Sunsynk: full-charge day chosen",
            (
                f"Chosen day: {best_day} ({scores[best_day]}). "
                f"Scores — Mon {scores['Monday']}, "
                f"Tue {scores['Tuesday']}, "
                f"Wed {scores['Wednesday']}, "
                f"Thu {scores['Thursday']}, "
                f"Fri {scores['Friday']}."
            ),
        )

    async def _async_weather_forecast(self, entity: str) -> list[dict[str, Any]]:
        """Daily forecast items of a weather entity ([] when the response has none); raises if the service call fails."""
        response = await self.hass.services.async_call(
            "weather", "get_forecasts", {"entity_id": entity, "type": "daily"},
            blocking=True, return_response=True,
        )
        data = response.get(entity) if isinstance(response, dict) else None
        return (data.get("forecast") or []) if isinstance(data, dict) else []

    async def _async_weekday_weather_scores(self) -> dict[str, float | None] | None:
        """Mon–Fri full-charge scores from the daily weather forecast.

        A weekday missing from the forecast scores None; None overall (with
        last_error set) when the weather service call fails.
        """
        weather_entity = self.cfg[CONF_WEATHER_ENTITY]
        try:
            forecast_items = await self._async_weather_forecast(weather_entity)
        except Exception as exc:  # pragma: no cover
            self.coordinator.update_state(last_error=f"Weather forecast failed: {exc}")
            return None

        scores: dict[str, float | None] = {day: None for day in FULL_CHARGE_DAY_OPTIONS}
        for item in forecast_items:
            try:
                dt_value = dt_util.parse_datetime(item.get("datetime"))
            except Exception:
                dt_value = None
            if dt_value is None:
                continue
            day_name = dt_value.strftime("%A")
            if day_name in scores:
                scores[day_name] = score_full_charge_day(item, day_name)
        return scores

    async def _async_today_cloud_cover(self) -> float | None:
        """Today's cloud cover % from the daily weather forecast (phase 82); None when unavailable."""
        entity = self._cfg_str(CONF_WEATHER_ENTITY) or DEFAULT_WEATHER_ENTITY
        try:
            items = await self._async_weather_forecast(entity)
        except Exception:  # noqa: BLE001 - optional input, degrade to None
            return None
        today = dt_util.now().date()
        for item in items:
            when = dt_util.parse_datetime(str(item.get("datetime") or ""))
            value = item.get("cloud_coverage")
            if when is not None and dt_util.as_local(when).date() == today:
                return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        return None

    async def async_recheck_full_charge_day(self) -> None:
        """Daily 18:00: move this week's full-charge day to tomorrow when
        Forecast.Solar says tomorrow's PV alone can reach 100% and its weather
        beats the chosen day's (planning.full_charge_day_move). At most once a week.
        """
        if self.operation_mode == "monitor":
            return
        cfg = self.cfg
        sensor_id = str(cfg.get(CONF_TOMORROW_FORECAST_SENSOR, DEFAULT_TOMORROW_FORECAST_SENSOR) or "").strip()
        if not sensor_id:
            return
        tomorrow = (dt_util.now() + timedelta(days=1)).strftime("%A")
        chosen = self.selected_full_charge_day
        if tomorrow not in FULL_CHARGE_DAY_OPTIONS or chosen not in FULL_CHARGE_DAY_OPTIONS:
            return
        if FULL_CHARGE_DAY_OPTIONS.index(chosen) <= FULL_CHARGE_DAY_OPTIONS.index(tomorrow):
            return  # chosen day is tomorrow or already passed — nothing to move

        raw_kwh = self._essential_state(sensor_id)
        tomorrow_kwh: float | None = None
        if raw_kwh is not None:
            paired_days = await self.data_logger.async_load_paired_days(days=30)
            correction = self.data_logger.compute_forecast_correction(paired_days)
            # Pessimistic of raw vs corrected, as for low-solar decisions.
            tomorrow_kwh = round(min(raw_kwh, raw_kwh * correction), 2)

        battery_capacity_kwh = self._capacity_kwh(cfg)
        avg_consumption_kw = self._regime_load_kw(cfg, bool(self.coordinator.state.away_mode))
        needed_kwh = solar_kwh_to_fill(battery_capacity_kwh, avg_consumption_kw)

        weather_scores = await self._async_weekday_weather_scores() or {}
        move, reason = full_charge_day_move(
            tomorrow=tomorrow,
            chosen=chosen,
            tomorrow_kwh=tomorrow_kwh,
            needed_kwh=needed_kwh,
            tomorrow_score=weather_scores.get(tomorrow),
            chosen_score=weather_scores.get(chosen),
            already_moved=bool(self.coordinator.state.full_charge_day_moved_to),
            weekdays=FULL_CHARGE_DAY_OPTIONS,
        )
        await self.data_logger.async_log_full_charge_recheck(
            chosen_day=chosen,
            tomorrow=tomorrow,
            tomorrow_kwh=tomorrow_kwh,
            needed_kwh=needed_kwh,
            tomorrow_score=weather_scores.get(tomorrow),
            chosen_score=weather_scores.get(chosen),
            moved=move,
            reason=reason,
        )
        if not move:
            return
        self.coordinator.update_state(selected_full_charge_day=tomorrow, full_charge_day_moved_to=tomorrow)
        await self.async_notify(
            "🔋 Sunsynk: full-charge day moved",
            (
                f"Moved from {chosen} to {tomorrow}: {tomorrow_kwh} kWh solar forecast "
                f"(needs {needed_kwh} kWh to fill from PV), weather score "
                f"{weather_scores.get(tomorrow)} vs {weather_scores.get(chosen)}."
            ),
        )

    def _synthetic_hourly_forecast(self, daily_kwh: float) -> dict[int, float] | None:
        """Approximate an hourly {hour: kWh} profile from a daily total.

        Distributes the day's kWh across daylight hours (sunrise→sunset from
        sun.sun) as a sine bell — see planning.synthetic_hourly_profile. This
        lets the solar-bridge walk model the morning ramp for users without a
        real hourly forecast sensor. Returns None if sun.sun or the daily total
        is unavailable, so callers fall back to the simple bridge.
        """
        if daily_kwh <= 0:
            return None
        sun = self._sun_times()
        if sun is None or sun[0] is None or sun[1] is None:
            return None
        rise, sett = sun
        return synthetic_hourly_profile(
            daily_kwh, rise.hour + rise.minute / 60.0, sett.hour + sett.minute / 60.0
        )

    def _get_hourly_forecast_kwh(self) -> dict[int, float] | None:
        """Return hourly solar forecast as {hour: kWh}, or None if unavailable/unconfigured.

        Supports two attribute formats:
        - Forecast.Solar: dict keyed by "YYYY-MM-DD HH:MM:SS" with float kWh per hour
        - Solcast: list of {"period_start": iso_str, "pv_estimate": kWh per 30-min period}
          (consecutive 30-min periods are summed to hourly buckets)
        """
        cfg = self.cfg
        sensor_id = self._cfg_str(CONF_HOURLY_FORECAST_SENSOR, cfg)
        state = self._available_state(sensor_id) if sensor_id else None
        if state is None:
            return None
        attr_name = str(cfg.get(CONF_HOURLY_FORECAST_ATTRIBUTE, DEFAULT_HOURLY_FORECAST_ATTRIBUTE)).strip()
        raw = state.attributes.get(attr_name)
        if not raw:
            return None

        hourly: dict[int, float] = {}

        if isinstance(raw, dict):
            # Forecast.Solar format: {"YYYY-MM-DD HH:MM:SS": kwh, ...}
            for key, val in raw.items():
                try:
                    hour = int(str(key).split(" ")[1].split(":")[0]) if " " in str(key) else int(str(key).split("T")[1].split(":")[0])
                    hourly[hour] = hourly.get(hour, 0.0) + float(val)
                except (ValueError, IndexError):
                    continue
        elif isinstance(raw, list):
            # Solcast format: [{"period_start": iso_str, "pv_estimate": kwh_per_30min}, ...]
            for item in raw:
                try:
                    period_start = str(item.get("period_start", ""))
                    val = float(item.get("pv_estimate", 0))
                    # Parse hour from ISO string (handles both "T" and " " separators)
                    time_part = period_start.split("T")[1] if "T" in period_start else period_start.split(" ")[1]
                    hour = int(time_part.split(":")[0])
                    hourly[hour] = hourly.get(hour, 0.0) + val
                except (ValueError, IndexError, AttributeError):
                    continue

        return hourly if hourly else None

    async def async_run_import_plan(
        self, source: str = "automatic", dry_run: bool = False, notify: bool = True
    ) -> dict[str, Any] | None:
        """Calculate and push overnight import plan.

        When ``dry_run`` is True the full plan is computed but nothing is pushed
        to the inverter, no record is logged, and persisted state is left
        untouched — the computed plan JSON is sent to the app notification only.
        This lets the user test the logic any time of day without side effects.
        """
        if self.operation_mode == "monitor" and not dry_run:
            self.coordinator.update_state(
                operation_mode="monitor",
                last_import_plan={"logic_branch": "monitor_only", "source": source},
            )
            return
        if not dry_run and self._outage_active():
            # Phase 48: no push can land during an outage; re-plan when the grid returns.
            self.coordinator.update_state(touch=False, grid_outage={**self.coordinator.state.grid_outage, "plan_skipped": True})
            return
        if not dry_run:
            self._cancel_plan_retry()  # a fresh plan supersedes any pending retry

        # Pre-flight: battery SOC is essential. If missing we cannot compute
        # energy_needed = (target - soc), so skip rather than default soc=0
        # which would size a full-window max-import.
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            msg = f"Battery SOC entity {self.battery_soc_entity} unavailable"
            now_local = dt_util.now()
            if not dry_run and soc_retry_due(now_local.hour * 60 + now_local.minute):
                _LOGGER.warning("%s — retrying the import plan in %d min", msg, SOC_RETRY_INTERVAL_MINUTES)
                self.coordinator.update_state(last_error=f"{msg} — import plan retrying")
                self._plan_retry = async_call_later(
                    self.hass, SOC_RETRY_INTERVAL_MINUTES * 60, partial(self._async_soc_plan_retry, source)
                )
                return
            await self._async_skip_plan(f"{msg} — import plan skipped")
            return

        cfg = self.cfg
        now = dt_util.now()
        today = now.strftime("%A")
        full_day = self.selected_full_charge_day
        is_full_day = today == full_day

        # Pre-flight: forecast is essential because a low forecast toggles the
        # maximum-import override. A missing forecast read as 0 would push 100%
        # target + full window unnecessarily.
        raw_forecast_kwh, forecast_fallback = self._resolve_raw_forecast(cfg[CONF_SOLAR_FORECAST_SENSOR], now)
        if raw_forecast_kwh is None:
            await self._async_skip_plan(
                f"Forecast sensor {cfg[CONF_SOLAR_FORECAST_SENSOR]} unavailable and no usable prior plan — "
                "import plan skipped"
            )
            return

        battery_capacity_kwh = self._capacity_kwh(cfg)
        charge_rate_kw = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        # Occupancy regime for this plan: home vs away (holiday). Drain and the
        # evening-SOC nudge are learned per-regime so a low-load holiday can't skew
        # the home profile; forecast correction stays global (load-independent).
        away = bool(self.coordinator.state.away_mode)
        is_weekend = today in ("Saturday", "Sunday")
        # Away takes precedence over weekday/weekend: on holiday the house load is
        # low regardless of the day, so the solar bridge sizes to the lower figure.
        avg_consumption_kw = self._regime_load_kw(cfg, away, is_weekend)

        paired_days = await self.data_logger.async_load_paired_days(days=30)
        # Phase 12: home nights use the overnight rate learned from the 06:00
        # load readings (clamped to 0.5x-2x config); away keeps its config rate.
        learned_kw, learned_load_days = (None, 0) if away else learned_load_kw(paired_days, now.date(), is_weekend)
        avg_consumption_kw, load_source = resolve_load_kw(avg_consumption_kw, learned_kw)
        if not dry_run:  # rule 17: the refresh assigns learned values and writes state
            await self._refresh_evening_load(paired_days, now.date(), is_weekend, away)
        forecast_correction = self.data_logger.compute_forecast_correction(paired_days)
        # When the forecast sensor was unavailable we already used yesterday's raw
        # value; don't apply the correction factor a second time on top of values
        # that may already have been corrected by the prior plan run.
        applied_correction = 1.0 if forecast_fallback else forecast_correction
        solar_forecast_kwh = round(raw_forecast_kwh * applied_correction, 2)
        band = forecast_band(solar_forecast_kwh)
        # Shadow only (phase 20): logged beside the live factor, not applied.
        weighted_correction, weighted_basis = weighted_forecast_correction(paired_days, now.date(), band)
        # Low-solar decisions key on the PESSIMISTIC of raw vs corrected. The
        # learned correction is derived mostly from good days; on a genuinely bad
        # day the raw forecast is already right, and multiplying it above the
        # threshold would skip the max-import override and leave the battery
        # short. If either forecast says a bad day, believe it.
        low_solar_forecast_kwh = min(raw_forecast_kwh, solar_forecast_kwh)
        low_solar = low_solar_forecast_kwh < LOW_SOLAR_THRESHOLD_KWH

        # Solar start = sunrise + configured offset. 05:00 is used as the
        # worst-case charge window end to avoid circularity.
        solar_start_time: str | None = None
        hours_to_solar: float | None = None
        sun = self._sun_times()
        if sun is not None and sun[0] is not None:
            solar_start_dt = sun[0] + timedelta(
                hours=float(cfg.get(CONF_SOLAR_START_OFFSET_HOURS, DEFAULT_SOLAR_START_OFFSET_HOURS))
            )
            solar_start_time = solar_start_dt.strftime("%H:%M")
            hours_to_solar = max(0.0, solar_start_dt.hour + solar_start_dt.minute / 60.0 - 5.0)

        # Phase 68: the forced weekly 100% is redundant when the battery reached 99% in the last 7 days.
        # Shadow-logged every night; only skipped when full_charge_skip_live is on.
        since_full = days_since_full(await self.data_logger.async_load_snapshot_days(30), now.date())
        full_due, _full_reason = full_charge_due(since_full)
        skip_would = is_full_day and not full_due
        if skip_would and cfg.get(CONF_FULL_CHARGE_SKIP_LIVE, DEFAULT_FULL_CHARGE_SKIP_LIVE) and self.operation_mode != "monitor":
            is_full_day = False
        decision = select_target_soc(
            is_full_day=is_full_day,
            low_solar_forecast_kwh=low_solar_forecast_kwh,
            band=band,
            hours_to_solar=hours_to_solar,
            avg_consumption_kw=avg_consumption_kw,
            battery_capacity_kwh=battery_capacity_kwh,
            hourly_forecast_kwh=self._get_hourly_forecast_kwh(),
            hourly_correction=applied_correction,
            # Ramp safety net built from the RAW (pessimistic) daily forecast.
            synthetic_profile=self._synthetic_hourly_forecast(raw_forecast_kwh),
        )
        target_soc = decision.target_soc

        overnight_drain_adjustment = 0
        soc_adjustment = 0
        feedback_adj, feedback_days, feedback_reason = 0, 0, "target_full"
        feedback_live = bool(cfg.get(CONF_IMPORT_FEEDBACK_LIVE, DEFAULT_IMPORT_FEEDBACK_LIVE))
        if target_soc < 100:
            # Drain and evening-nudge corrections apply whenever the target has
            # headroom — regular nights and full-charge-day solar-bridge plans.
            overnight_drain_adjustment = self.data_logger.compute_overnight_drain_adjustment(paired_days, away)
            soc_adjustment = self.data_logger.compute_soc_target_adjustment(paired_days, band, away)
            # Phase 15: day-rate import feedback; logged only (shadow) unless
            # the live option is on, where it replaces the evening-SOC nudge.
            feedback_adj, feedback_days, feedback_reason = import_feedback_adjustment(
                paired_days, now.date(), band, away
            )
            nudge = feedback_adj if feedback_live else soc_adjustment
            target_soc = apply_soc_adjustments(target_soc, overnight_drain_adjustment, nudge)
        # Phase 64 (shadow): the hourly bridge re-planned at the newsvendor quantile of
        # the logged actual/forecast ratios. Logged only; never pushed.
        nv = self._newsvendor_shadow(
            decision, forecast_fallback, paired_days, now.date(), band, avg_consumption_kw, battery_capacity_kwh,
            overnight_drain_adjustment, feedback_adj if feedback_live else soc_adjustment,
        )
        # Phase 70 (shadow): learned P10/P50/P90 of today's solar; the decision above keeps min(raw, corrected).
        quantile_samples = forecast_ratio_samples(
            await self.data_logger.async_load_paired_days(days=FORECAST_QUANTILE_HISTORY_DAYS)
        )
        solar_quantiles = None if forecast_fallback else forecast_quantiles(
            quantile_samples, now.month, band, raw_forecast_kwh
        )
        # Phase 82 (shadow): the same quantiles binned by today's cloud cover; the target is unchanged.
        cloud_cover_pct = await self._async_today_cloud_cover()
        cloud_quantiles = None if forecast_fallback or cloud_bucket(cloud_cover_pct) is None else forecast_quantiles(
            quantile_samples, now.month, band, raw_forecast_kwh, cloud=cloud_bucket(cloud_cover_pct)
        )
        # A free event later today should start at its floor: buy only that plus the load until then.
        uncapped_target_soc = target_soc
        free_event = self.coordinator.state.free_event
        free_start_dt = dt_util.parse_datetime(free_event.get("free_start") or "")
        target_soc = free_event_target_cap(
            target_soc, free_event, dt_util.as_local(free_start_dt) if free_start_dt else None, now.date(),
            avg_consumption_kw, battery_capacity_kwh,
        )

        # Phase 46: a one-night user override (service) wins over every other cap.
        target_soc, user_override = apply_user_override(
            target_soc, self.coordinator.state.user_override, now.date().isoformat(), soc
        )

        computed_charge_rate = self.data_logger.compute_effective_charge_rate_kw(
            paired_days, battery_capacity_kwh, overnight_drain_adjustment
        )
        # When calibration thins below its minimum (common in summer — charge gaps
        # rarely reach the 10% needed to calibrate), reuse the last learned rate
        # rather than the optimistic nameplate config, which under-sizes the window.
        # Fallback chain: fresh computation → persisted last-known → most recent
        # non-null rate in history (seeds the persisted value on first run).
        effective_charge_rate = computed_charge_rate
        if effective_charge_rate is None:
            effective_charge_rate = self.coordinator.state.last_effective_charge_rate_kw
        if effective_charge_rate is None:
            effective_charge_rate = self.data_logger.last_known_charge_rate_kw(paired_days)
        battery_temp_c = self._state_float(self.battery_temp_entity, 20.0)
        # Phase 61: rate measured from the 02:00/05:00 meters; logged always, used when live.
        measured = self.data_logger.compute_measured_charge_rate_kw(paired_days, battery_temp_c)
        measured_live = bool(cfg.get(CONF_MEASURED_RATE_LIVE, DEFAULT_MEASURED_RATE_LIVE))
        derate = True
        if measured_live and measured is not None:
            effective_charge_rate, at_temp = measured
            derate = not at_temp
        used_charge_rate, temp_factor = resolve_used_charge_rate(
            charge_rate_kw, effective_charge_rate, battery_temp_c, derate
        )

        # Phase 13: nameplate rate loses ~8% to conversion; in-window house load
        # adds grid kWh (logged) but not window time.
        efficiency = charge_efficiency(charge_rate_kw, effective_charge_rate)
        energy_needed_kwh = max(0.0, (target_soc - soc) / 100.0 * battery_capacity_kwh)
        if low_solar:
            # Solar is scarce — take all the cheap import we can get.
            end_minutes = 5 * 60
            logic_branch = "low_solar_full_window"
        else:
            # Physics-based window: charge exactly as long as needed to reach target.
            end_minutes = flux1_end_minutes(energy_needed_kwh / efficiency, used_charge_rate)
            logic_branch = "adaptive_hourly" if decision.hourly_forecast_used else "adaptive"
        # Phase 62: hold the target to 05:00 so the grid, not the battery, runs the house.
        full_window_live = bool(cfg.get(CONF_FULL_WINDOW_LIVE, DEFAULT_FULL_WINDOW_LIVE))
        full_window_end = "05:00"
        if full_window_live and not low_solar and end_minutes < 5 * 60 and energy_needed_kwh > 0:
            end_minutes = 5 * 60
            logic_branch = "full_window"
        flux1_end = minutes_to_hhmm(end_minutes)
        # Phase 23: the lowest grid-charge current that still reaches the target
        # by 05:00. Logged only unless the live option is on; then the current
        # is written first and the window stays open to 05:00.
        battery_voltage = float(cfg.get(CONF_BATTERY_VOLTAGE) or DEFAULT_BATTERY_VOLTAGE)
        gentle_max_a = max_charge_current_a(charge_rate_kw, battery_voltage)
        gentle_current_a = gentle_charge_current_a(energy_needed_kwh, CHEAP_WINDOW_HOURS, battery_voltage, gentle_max_a)
        gentle_charge_applied, inverter_grid_current_a = False, None
        if not dry_run and self._gentle_charge_due(cfg, now, gentle_current_a, gentle_max_a):
            gentle_charge_applied, inverter_grid_current_a = await self._async_apply_gentle_charge(gentle_current_a, now)
            if gentle_charge_applied:
                end_minutes = hhmm_to_minutes(CHEAP_WINDOW_END)
                flux1_end = CHEAP_WINDOW_END
        window_load_kwh, grid_kwh_needed = window_grid_kwh(
            energy_needed_kwh, efficiency, avg_consumption_kw, end_minutes
        )
        next_import_window = f"02:00→{flux1_end}"

        payload = {
            "flux_1": {"startTime": "02:00", "endTime": flux1_end, "targetSoc": target_soc},
            "flux_2": dict(DEFAULT_FLUX2_SLOT),
        }
        # A free event holds Flux 1/2 for its own windows; the
        # plan is still computed and logged so it's ready to push the moment the
        # event ends (_async_restore_normal_plan reads it back).
        held_by = None if dry_run else self._slots_held_by()
        api_ok = None if (dry_run or held_by) else await self.async_push_flux_override(payload)
        retrying = api_ok is False and self._arm_plan_retry(payload, source, now, attempts=0)

        plan_state = {
            "date": now.date().isoformat(),
            "version": INTEGRATION_VERSION,
            "today": today,
            "selected_full_charge_day": full_day,
            "is_full_day": is_full_day,
            "soc": soc,
            "raw_forecast_kwh": raw_forecast_kwh,
            "forecast_correction_factor": forecast_correction,
            "forecast_correction_weighted": weighted_correction,
            "forecast_correction_weighted_basis": weighted_basis,
            **nv,
            "forecast_quantiles": solar_quantiles,
            "forecast_quantiles_cloud": cloud_quantiles,
            "cloud_cover_pct": cloud_cover_pct,
            "outdoor_temp_c": self._outside_temp_now(),
            "days_since_full": since_full,
            "full_charge_due": full_due,
            "full_charge_skip_would": skip_would,
            "solar_forecast_kwh": solar_forecast_kwh,
            "low_solar_forecast_kwh": round(low_solar_forecast_kwh, 2),
            "forecast_band": band,
            "logic_branch": logic_branch,
            "full_window_end": full_window_end,
            "full_window_live": full_window_live and logic_branch == "full_window",
            "solar_start_time": solar_start_time,
            "hours_to_solar": round(hours_to_solar or 0.0, 2),
            "target_soc": target_soc,
            "target_soc_reason": decision.reason,
            "overnight_drain_adjustment": overnight_drain_adjustment,
            "overnight_drain_days": self.data_logger.count_drain_adjustment_days(paired_days, away),
            "soc_adjustment": soc_adjustment,
            "soc_adjustment_days": self.data_logger.count_soc_adjustment_days(paired_days, band, away),
            "import_feedback_adjustment": feedback_adj,
            "import_feedback_days": feedback_days,
            "import_feedback_reason": feedback_reason,
            "import_feedback_live": feedback_live,
            "forecast_correction_days": self.data_logger.count_forecast_correction_days(paired_days),
            "effective_charge_rate_kw": effective_charge_rate,
            "charge_rate_from_cache": computed_charge_rate is None,
            "used_charge_rate_kw": used_charge_rate,
            "charge_efficiency": efficiency,
            "energy_needed_kwh": round(energy_needed_kwh, 2),
            "window_load_kwh": window_load_kwh,
            "grid_kwh_needed": grid_kwh_needed,
            "charge_rate_calibration_days": self.data_logger.count_charge_rate_calibration_days(paired_days),
            "flux1_end": flux1_end,
            "next_import_window": next_import_window,
            "payload": payload,
            "source": source,
            "api_ok": api_ok,
            "retrying": retrying,
            "held_by_free_event": held_by == "free_event",
            "free_event_capped": target_soc < uncapped_target_soc and user_override is None,
            "user_override": user_override,
            "battery_voltage": battery_voltage,
            "gentle_current_a": gentle_current_a,
            "gentle_max_current_a": round(gentle_max_a, 1),
            "gentle_charge_rate_kw": (
                round(gentle_current_a * battery_voltage / 1000, 2) if gentle_current_a is not None else None
            ),
            "gentle_charge_applied": gentle_charge_applied,
            "inverter_grid_current_a": inverter_grid_current_a,
            "forecast_fallback": forecast_fallback,
            "is_weekend": is_weekend,
            "away": away,
            "avg_consumption_kw": avg_consumption_kw,
            "load_source": load_source,
            "learned_load_kw": learned_kw,
            "learned_load_days": learned_load_days,
            "battery_temp_c": battery_temp_c,
            "temp_deration_factor": temp_factor,
            "measured_charge_rate_kw": measured[0] if measured else None,
            "measured_charge_rate_live": measured_live and measured is not None,
            "hourly_forecast_used": decision.hourly_forecast_used,
            "synthetic_ramp": decision.synthetic_ramp,
            "bridge_hour": decision.bridge_hour,
        }

        if dry_run:
            if notify:
                await self.async_notify(
                    f"🧪 Sunsynk: test plan (dry run) — {plan_state['date']}",
                    "\n".join(explain_plan(plan_state)) + "\n\n" + json.dumps(plan_state),
                )
            return plan_state

        if should_log_import_plan(source, now):
            await self.data_logger.async_log_import_plan(plan_state)
        if source != STARTUP_PLAN_SOURCE:
            self.coordinator.update_state(touch=False, nightly_import_plan=plan_state)

        self.coordinator.update_state(
            current_soc_target=target_soc,
            next_import_window=next_import_window,
            last_import_plan=plan_state,
            operation_mode=self.operation_mode,
            # Clear any stale error (e.g. a transient "SOC unavailable" skip from a
            # reload) once a plan completes and pushes cleanly. Keep the error when
            # the push failed — _async_post_with_status already set it and the user
            # needs to see the inverter did not receive the plan.
            last_error=None if api_ok else self.coordinator.state.last_error,
            # Remember the freshest known rate so a later plan can reuse it when
            # calibration thins (locks in the history seed on first run).
            last_effective_charge_rate_kw=effective_charge_rate,
        )
        if source != STARTUP_PLAN_SOURCE:
            await self._guarded(lambda: self.async_run_tou_shadow(plan_state), "Native TOU shadow")
        await self._async_notify_import_plan(plan_state)
        if source != STARTUP_PLAN_SOURCE:
            await self._async_log_multi_day_shadow("plan", target_soc=target_soc)

    def _cancel_plan_retry(self) -> None:
        if self._plan_retry:
            self._plan_retry()
            self._plan_retry = None

    def _arm_plan_retry(self, payload: dict[str, Any], source: str, now, attempts: int) -> bool:
        """Arm the next failed-plan-push retry (phase 32); False when none is due."""
        if not plan_retry_due(now.hour * 60 + now.minute, attempts):
            return False
        self._plan_retry = async_call_later(
            self.hass,
            PLAN_RETRY_INTERVAL_MINUTES * 60,
            partial(self._async_plan_retry, payload, source, attempts + 1),
        )
        return True

    async def _async_soc_plan_retry(self, source: str, _now) -> None:
        self._plan_retry = None
        await self._guarded(lambda: self.async_run_import_plan(source=source), "Import plan SOC retry")

    async def _async_plan_retry(self, payload: dict[str, Any], source: str, attempt: int, _now) -> None:
        self._plan_retry = None
        await self._guarded(lambda: self.async_run_plan_retry(payload, source, attempt), "Import plan retry")

    async def async_run_plan_retry(self, payload: dict[str, Any], source: str, attempt: int) -> None:
        """Re-push a failed overnight plan; stop on success, held slots or monitor mode."""
        if self.operation_mode == "monitor" or self._slots_held() or self._outage_active():
            return
        now = dt_util.now()
        self.coordinator.api.stats.note_push_retry()  # phase 101
        if await self.async_push_flux_override(payload):
            state = self.coordinator.state
            updates: dict[str, Any] = {"last_error": None}
            if isinstance(state.last_import_plan, dict) and state.last_import_plan.get("payload") == payload:
                updates["last_import_plan"] = {**state.last_import_plan, "api_ok": True, "retrying": False}
            plan = state.nightly_import_plan
            if source != STARTUP_PLAN_SOURCE and isinstance(plan, dict) and plan.get("payload") == payload:
                updates["nightly_import_plan"] = {**plan, "api_ok": True, "retrying": False}
            self.coordinator.update_state(**updates)
            flux1 = payload.get("flux_1") or {}
            await self.async_notify(
                f"🔋 Sunsynk: import plan set (retry {attempt})",
                f"Import: {flux1.get('startTime')} → {flux1.get('endTime')} target {flux1.get('targetSoc')}%.",
            )
            return
        if not self._arm_plan_retry(payload, source, now, attempt):
            await self.async_notify(
                "⚠️ Sunsynk: import plan NOT applied",
                f"Inverter NOT updated after {attempt} retries; tonight's charge runs on the existing settings.",
            )

    async def _async_skip_plan(self, msg: str) -> None:
        _LOGGER.warning(msg)
        self.coordinator.update_state(last_error=msg)
        await self.async_notify("⚠️ Sunsynk: plan skipped", msg)

    def _resolve_raw_forecast(self, forecast_entity: str, now) -> tuple[float | None, bool]:
        """Return (raw_forecast_kwh, used_fallback).

        Falls back to the last plan's raw forecast when the sensor is down, but
        only if that plan is from today or yesterday. (None, False) means no
        usable forecast at all.
        """
        value = self._essential_state(forecast_entity)
        if value is not None:
            return value, False
        last_plan = self.coordinator.state.last_import_plan
        if not isinstance(last_plan, dict):
            return None, False
        prior_raw = last_plan.get("raw_forecast_kwh")
        recent_dates = (now.strftime("%Y-%m-%d"), (now - timedelta(days=1)).strftime("%Y-%m-%d"))
        if not isinstance(prior_raw, (int, float)) or last_plan.get("date") not in recent_dates:
            return None, False
        return float(prior_raw), True

    async def _async_notify_import_plan(self, plan: dict[str, Any]) -> None:
        correction = plan["forecast_correction_factor"]
        forecast_note = (
            f" (raw {round(plan['raw_forecast_kwh'], 1)} kWh ×{correction})" if correction != 1.0 else ""
        )
        fallback_note = " (using yesterday's forecast — sensor unavailable)" if plan["forecast_fallback"] else ""
        adjustment_parts = []
        if plan["overnight_drain_adjustment"]:
            adjustment_parts.append(f"drain +{plan['overnight_drain_adjustment']}%")
        if plan["soc_adjustment"]:
            adjustment_parts.append(f"eve {plan['soc_adjustment']:+d}%")
        adjustment_note = f" ({', '.join(adjustment_parts)})" if adjustment_parts else ""
        if plan.get("gentle_charge_applied"):
            adjustment_note += f" at {plan['gentle_current_a']} A"
        api_ok = plan["api_ok"]
        if plan.get("held_by_free_event"):
            title = "🔋 Sunsynk: import plan computed (held for free event)"
            api_note = " A free event holds the Flux slots; this plan pushes when it ends."
        else:
            title = "🔋 Sunsynk: import plan set" if api_ok else "⚠️ Sunsynk: import plan NOT applied"
            if api_ok:
                api_note = ""
            elif plan.get("retrying"):
                api_note = f" Inverter NOT updated; retrying every {PLAN_RETRY_INTERVAL_MINUTES} min."
            else:
                api_note = " Inverter NOT updated; will retry next cycle."
        full_day_note = " — full-charge day" if plan["is_full_day"] else ""
        away_note = " (away)" if plan["away"] else ""
        # "summer_like" → "summer-like": keep internal band names out of user text.
        season = plan["forecast_band"].replace("_", "-")
        fallback_logic_note = "" if plan["logic_branch"].startswith("adaptive") else " (fallback logic)."
        await self.async_notify(
            title,
            (
                f"Today: {plan['today']}{full_day_note}{away_note}. "
                f"SOC: {round(plan['soc'], 1)}%. "
                f"Solar forecast: {round(plan['solar_forecast_kwh'], 1)} kWh{forecast_note}{fallback_note}. "
                f"Import: 02:00 → {plan['flux1_end']} target {plan['target_soc']}%{adjustment_note}. "
                f"Season: {season}.{fallback_logic_note}{api_note}"
                + (f"\nWhy: {' '.join(explain_plan(plan))}" if explain_plan(plan) else "")
            ),
        )

    async def _async_choose_best_full_charge_day(self, _now) -> None:
        """Time-change callback at 18:00 daily: Sunday weekly pick, then the daily re-check."""
        if dt_util.now().strftime("%A") == "Sunday":
            await self._guarded(self.async_choose_best_full_charge_day, "Full-charge-day selection")
            await self._guarded(self._async_send_weekly_cost_summary, "Weekly cost summary")
            await self._guarded(self._async_send_shadow_scorecard, "Shadow scorecard")
            await self._guarded(self._async_send_ai_weekly_insight, "AI weekly insight")
        await self._guarded(self._async_monthly_reports, "Monthly reports")
        await self._guarded(self.async_recheck_full_charge_day, "Full-charge-day re-check")

    async def _async_run_import_plan(self, _now) -> None:
        """Time-change callback at 01:55 daily."""
        await self._guarded(self._async_auto_away, "Auto away check")
        await self._guarded(self.async_run_import_plan, "Scheduled import plan")

    async def _async_charge_watchdog(self, _now) -> None:
        """Time-change callback at 02:20 daily."""
        await self._guarded(self.async_run_charge_watchdog, "Charge watchdog")

    def _charge_watchdog_plan(self, now) -> dict[str, Any] | None:
        """Tonight's pushed plan when it has a charge to watch, else None.

        Skipped in monitor mode (no API writes), while a free event or saving
        session holds the Flux slots, when the 01:55 push never ran, or when no charge was planned.
        """
        if self.operation_mode == "monitor" or self._slots_held():
            return None
        plan = self.coordinator.state.nightly_import_plan or {}
        if plan.get("date") != now.date().isoformat() or plan.get("api_ok") is None:
            return None
        soc, target = plan.get("soc"), plan.get("target_soc")
        if soc is None or target is None or target <= soc or not plan.get("payload"):
            return None
        return plan

    async def async_run_charge_watchdog(self) -> None:
        """Check the overnight charge is running; re-push the plan once if stalled."""
        now = dt_util.now()
        plan = self._charge_watchdog_plan(now)
        now_minutes = now.hour * 60 + now.minute
        end_minutes = hhmm_to_minutes(plan.get("flux1_end")) if plan else None
        if plan is None or end_minutes is None or end_minutes <= now_minutes:
            return
        capacity = self._capacity_kwh()
        soc_now = self._essential_state(self.battery_soc_entity)
        grid_import_w = self._essential_state(self.grid_pac_entity)
        result = charge_progress_ok(
            plan["soc"], soc_now, now_minutes - 2 * 60, _watchdog_rate_kw(plan),
            capacity, grid_import_w, plan["target_soc"],
        )
        record = {
            "date": plan["date"],
            "soc_start": plan["soc"],
            "target_soc": plan["target_soc"],
            "expected_rate_kw": _watchdog_rate_kw(plan),
            "first_check": {"soc_now": soc_now, "grid_import_w": grid_import_w, "result": result},
        }
        if result != "stalled":
            await self.data_logger.async_log_charge_watchdog(
                **record, result=result, retried=False, soc_now=soc_now, grid_import_w=grid_import_w
            )
            return
        retry_ok = await self.async_push_flux_override(plan["payload"])
        retry = {**record, "retry_api_ok": retry_ok, "retry_minutes": now_minutes, "retry_soc": soc_now}
        self._charge_watchdog_recheck = async_call_later(
            self.hass,
            CHARGE_WATCHDOG_RECHECK_MINUTES * 60,
            partial(self._async_charge_watchdog_recheck, retry),
        )

    async def _async_charge_watchdog_recheck(self, retry: dict[str, Any], _now) -> None:
        self._charge_watchdog_recheck = None
        await self._guarded(lambda: self.async_run_charge_watchdog_recheck(retry), "Charge watchdog re-check")

    async def async_run_charge_watchdog_recheck(self, retry: dict[str, Any]) -> None:
        """Judge the charge again after the one retry; notify if still stalled."""
        await self.async_check_inverter_events()
        now = dt_util.now()
        plan = self._charge_watchdog_plan(now)
        if plan is None:
            return
        capacity = self._capacity_kwh()
        soc_now = self._essential_state(self.battery_soc_entity)
        grid_import_w = self._essential_state(self.grid_pac_entity)
        # If the window ended before the re-check, judge only the minutes it was open.
        end_minutes = hhmm_to_minutes(plan.get("flux1_end")) or 0
        elapsed = min(now.hour * 60 + now.minute, end_minutes) - retry["retry_minutes"]
        result = charge_progress_ok(
            retry["retry_soc"], soc_now, elapsed, _watchdog_rate_kw(plan),
            capacity, grid_import_w, plan["target_soc"],
        )
        fields = {k: v for k, v in retry.items() if k not in ("retry_minutes", "retry_soc")}
        await self.data_logger.async_log_charge_watchdog(
            **fields,
            result="recovered" if result == "ok" else result,
            retried=True,
            soc_now=soc_now,
            grid_import_w=grid_import_w,
        )
        if result != "stalled":
            return
        pushed = (
            "The plan was re-pushed once but the charge still isn't running."
            if retry["retry_api_ok"]
            else "Re-pushing the plan FAILED — the inverter may not have the charge window."
        )
        await self.async_notify(
            "⚠️ Sunsynk: overnight charge not running",
            f"{pushed} SOC {plan['soc']}% at 01:55 → {soc_now}% now (target {plan['target_soc']}%, "
            f"window {plan.get('next_import_window')}). Grid import {round(grid_import_w)} W. "
            "Check the inverter's time-of-use settings." + (f" {bms}" if (bms := await self._async_bms_line()) else ""),
        )

    async def async_run_charge_outcome(self) -> None:
        """Log reached-vs-target for tonight's charge from the 05:00 snapshot (phase 54); read-only."""
        now = dt_util.now()
        plan = self._charge_watchdog_plan(now)
        if plan is None:
            return
        date = now.date().isoformat()
        snap = (await self.data_logger.async_load_meter_snapshots(date)).get("05:00") or {}
        outcome = charge_outcome(plan.get("target_soc"), snap.get("soc"), plan.get("battery_temp_c"))
        if outcome is None:
            return
        history = [
            r for r in await self.data_logger.async_load_records(14) if r.get("type") == "charge_outcome"
        ]
        await self.data_logger.async_log_charge_outcome(
            date=date, flux1_end=plan.get("flux1_end"),
            achievable_max_soc=achievable_max_soc(history + [outcome]), **outcome,
        )
        if outcome["alert"]:
            await self.async_notify(
                "🔋 Sunsynk: overnight charge stopped short",
                f"Target {outcome['target_soc']:.0f}%, reached {outcome['reached_soc']:.0f}% "
                f"({outcome['shortfall_pct']:.0f} points short). Likely cause: {outcome['likely_cause']}."
                + (f" {bms}" if (bms := await self._async_bms_line()) else ""),
            )

    async def _async_overnight_load_check(self) -> None:
        """05:00: log the 00:00-05:00 house load and alert if it is far above normal (phase 26).

        Read-only (notification only), so it runs in monitor mode too. A missing
        meter logs nothing and never alerts.
        """
        load = round_or_none(self._essential_state(self.day_load_entity))
        if load is None:
            return
        today = dt_util.now().date().isoformat()
        away = bool(self.coordinator.state.away_mode)
        history = await self.data_logger.async_recent_overnight_loads(today)
        await self.data_logger.async_log_overnight_load(date=today, load_kwh=load, away=away)
        if away or not self.cfg.get(CONF_BASELOAD_ALERT, DEFAULT_BASELOAD_ALERT):
            return
        anomaly = baseload_anomaly(history, load)
        if anomaly:
            await self.async_notify(
                "🔋 Sunsynk: overnight load high",
                f"Overnight load {anomaly['tonight_kwh']} kWh vs usual {anomaly['usual_kwh']} kWh "
                "— something left on?",
            )

    def _wear_pence(self) -> float | None:
        """Battery wear p/kWh from the cost and lifetime options; None when blank (phase 24)."""
        cfg = self.cfg
        try:
            return wear_cost_pence_per_kwh(
                float(cfg.get(CONF_BATTERY_COST_GBP) or 0), float(cfg.get(CONF_BATTERY_LIFETIME_KWH) or 0)
            )
        except (TypeError, ValueError):
            return None

    async def _async_review_battery_floor(self, date: str) -> dict[str, Any]:
        """22:00: read the inverter's low-battery SOC and log what a lower floor would have saved (phase 84).

        Read-only (also in monitor mode): the setting is never written. When the settings read-back
        exposes no floor the record still lists the candidate key names, so feasibility is visible."""
        settings = await self._async_api_read("Battery floor", self.coordinator.api.async_read_settings, self.inverter_serial)
        floor, key, names = battery_floor_soc(settings)
        paired = await self.data_logger.async_load_paired_days(days=30)
        prices, _sources = self._tariff_prices(dt_util.parse_date(date))
        saving = floor_saving_gbp(paired, floor, FLOOR_CANDIDATE_SOC, self._capacity_kwh(self.cfg), prices)
        record = {
            "date": date, "battery_floor_soc": floor, "floor_key": key, "floor_keys": names,
            "candidate_soc": FLOOR_CANDIDATE_SOC, "window_days": saving["days"], "hit_days": saving["hit_days"],
            "saving_30d_gbp": saving["saving_gbp"],
        }
        await self.data_logger.async_log_battery_floor(**record)
        return {"type": "battery_floor", **record}

    async def async_service_get_plan(self) -> dict[str, Any]:
        """Service: the dry-run plan as a response (rule 17: no push, log or state, no notification)."""
        plan = await self.async_run_import_plan(source="service", dry_run=True, notify=False)
        return {"plan": plan or {}}

    def _trim_target(self, soc: float, now_local: datetime) -> tuple[int | None, int]:
        """(sell-down target or None, evening reserve) for the daytime and full-day trims."""
        reserve = self._evening_reserve_soc()
        plan = self.coordinator.state.nightly_import_plan or {}
        plan_target = plan.get("target_soc") if plan.get("date") == now_local.date().isoformat() else None
        hold = self._cover_hold_slot(now_local)
        target = trim_target_soc(
            soc, reserve, minutes=now_local.hour * 60 + now_local.minute, plan_target_soc=plan_target,
            hold_soc=(hold or {}).get("targetSoc"),
        )
        return target, reserve

    async def _refresh_evening_load(self, paired_days: list[dict[str, Any]], today, weekend: bool, away: bool) -> None:
        """Phase 55: learn the 16:00-22:00 load from the meter snapshots (None below 7 home days)."""
        if away:
            return
        days = await self.data_logger.async_load_snapshot_days(30)
        skip = {d["date"] for d in paired_days if d.get("away") and d.get("date")}
        self._evening_load_kw, _n = learned_evening_load_kw(days, today, weekend, skip)
        await self._guarded(lambda: self._refresh_load_profile(days, today, weekend, skip), "Load profile")

    async def _refresh_load_profile(self, days, today, weekend: bool, skip: set[str]) -> None:
        """Phase 71: per-slot median load for today's day type, scaled by today's outside temperature."""
        half_import, _export = await self.data_logger.async_load_half_hourly(30)
        temps = await self._async_outside_temp_by_date(30)
        self._load_profile = load_profile(days, half_import, today, weekend, skip, temps, self._outside_temp_now())
        self.coordinator.update_state(
            touch=False, load_profile={**(self._load_profile or {}), "date": today.isoformat()}
        )

    def _evening_reserve_pair(self, hours: float | None = None) -> tuple[int, int | None]:
        """(reserve in force, learned-load reserve or None); learned only in force when live."""
        cfg = self.cfg
        kwargs = {} if hours is None else {"hours": hours}
        profile = self._profile_live()
        if profile is not None:  # phase 71 live: the profile's 19:00-02:00 rate wins
            return evening_reserve_choice(
                self._plan_load_kw(cfg), profile["evening_kw"], self._capacity_kwh(cfg), True, **kwargs
            )
        return evening_reserve_choice(
            self._plan_load_kw(cfg), self._evening_load_kw, self._capacity_kwh(cfg),
            bool(cfg.get(CONF_EVENING_LOAD_LIVE, DEFAULT_EVENING_LOAD_LIVE)), **kwargs,
        )

    def _evening_reserve_soc(self) -> int:
        """Phase 16: SOC to hold at 19:00 so the house runs to the 02:00 off-peak start.

        Load is tonight's plan rate (phase 12 learned or config); with
        ``evening_load_live`` the learned 16:00-22:00 rate (phase 55).
        """
        return self._evening_reserve_pair()[0]

    async def _async_initial_capacity_estimate(self, _now) -> None:
        await self._guarded(lambda: self.async_run_capacity_estimate(notify=False), "Battery capacity estimate")

    async def async_run_capacity_estimate(self, notify: bool = True) -> dict[str, Any]:
        """Measure usable capacity from recent meter snapshots (phase 34); read-only, shadow.

        Logs a `capacity_estimate` record and alerts when the estimate is below
        90% of the configured `battery_capacity`. Never changes the config.
        """
        days = await self.data_logger.async_load_snapshot_days(CAPACITY_HISTORY_DAYS)
        samples = [
            sample
            for date, snaps in days.items()
            for sample in capacity_samples(snaps, int(date[5:7]))
        ]
        estimate = capacity_estimate(samples)
        configured = self._capacity_kwh()
        record = {
            "date": dt_util.now().date().isoformat(),
            "estimate_kwh": estimate,
            "samples": len(samples),
            "charge_samples": sum(1 for s in samples if s["kind"] == "charge"),
            "configured_kwh": configured,
            "pct_of_configured": None if estimate is None else round(estimate / configured * 100, 1),
        }
        self.coordinator.update_state(touch=False, battery_capacity_estimate=record)
        if not notify:
            return record
        await self.data_logger.async_log_capacity_estimate(**record)
        if capacity_low(estimate, configured):
            await self.async_notify(
                "🔋 Sunsynk: battery capacity low",
                f"Measured usable capacity ~{estimate} kWh is {record['pct_of_configured']}% of the configured "
                f"{configured:g} kWh ({len(samples)} charge/discharge samples, last {CAPACITY_HISTORY_DAYS} days). "
                "Check the battery, or lower Battery capacity in Configure so plans use the real figure.",
            )
        return record

    def _gentle_charge_due(self, cfg: dict[str, Any], now: datetime, current_a: int | None, max_a: float) -> bool:
        """Live gentle charge applies before 05:00 when it actually lowers the current."""
        return (
            bool(cfg.get(CONF_GENTLE_CHARGE_LIVE, DEFAULT_GENTLE_CHARGE_LIVE))
            and current_a is not None
            and current_a < max_a
            and not self._slots_held()
            and now.hour * 60 + now.minute < hhmm_to_minutes(CHEAP_WINDOW_END)
        )

    async def _async_apply_gentle_charge(self, current_a: int, now: datetime) -> tuple[bool, float | None]:
        """Write the gentle grid-charge current and arm its 05:00 restore.

        Returns (applied, the user's own grid-charge current). The user's value
        is the ceiling: nothing is written unless the gentle current is lower.
        """
        pending = self.coordinator.state.gentle_charge
        # A re-plan before 05:00 (reload) reads back our own gentle value; the
        # original one stays both the ceiling and the value to restore.
        held = pending.get("restore_current_a") if pending.get("phase") == "applied" else None
        ok, previous = await self._async_write_setting(
            GENTLE_CHARGE_SETTING,
            current_a,
            write_if=lambda inverter_a: gentle_current_lowers(current_a, held if held is not None else inverter_a),
        )
        user_a = held if held is not None else previous
        if not ok:
            return False, user_a
        restore = user_a
        restore_at = now.replace(hour=5, minute=0, second=0, microsecond=0)
        self.coordinator.update_state(
            gentle_charge={
                "phase": "applied",
                "date": now.date().isoformat(),
                "current_a": current_a,
                "restore_current_a": restore,
                "restore_at": restore_at.isoformat(),
                "restore_failures": 0,
            }
        )
        self._arm_gentle_restore()
        return True, user_a

    def _arm_gentle_restore(self) -> None:
        if self._gentle_restore_unsub:
            self._gentle_restore_unsub()
        restore_at = dt_util.as_local(dt_util.parse_datetime(self.coordinator.state.gentle_charge["restore_at"]))
        delay = max(0, elapsed_seconds(restore_at, dt_util.now()))
        self._gentle_restore_unsub = async_call_later(self.hass, delay, self._async_gentle_restore)

    async def _async_gentle_restore(self, _now) -> None:
        self._gentle_restore_unsub = None
        await self._guarded(self.async_restore_charge_current, "Grid charge current restore")

    async def async_restore_charge_current(self) -> None:
        """05:00: put the inverter's grid-charge current back; retried every 30 minutes until it sticks.

        Runs in monitor mode too — it only undoes this integration's own write.
        """
        gentle = dict(self.coordinator.state.gentle_charge)
        if gentle.get("phase") != "applied":
            return
        restore_at = dt_util.parse_datetime(gentle.get("restore_at") or "")
        if restore_at is not None and dt_util.now() < dt_util.as_local(restore_at):
            return
        ok, _ = await self._async_write_setting(GENTLE_CHARGE_SETTING, gentle["restore_current_a"])
        if ok:
            gentle["phase"] = "restored"
            self.coordinator.update_state(gentle_charge=gentle)
            return
        gentle["restore_failures"] = gentle.get("restore_failures", 0) + 1
        self.coordinator.update_state(gentle_charge=gentle)
        if gentle["restore_failures"] == 1:
            await self.async_notify(
                "⚠️ Sunsynk: grid charge current NOT restored",
                f"Still {gentle['current_a']} A after the gentle overnight charge; retrying every 30 minutes. "
                f"If this persists, set Grid charge back to {gentle['restore_current_a']} A in the Sunsynk app.",
            )
