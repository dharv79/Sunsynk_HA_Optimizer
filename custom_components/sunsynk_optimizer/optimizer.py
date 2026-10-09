# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Core optimizer logic."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AVG_CONSUMPTION_KW,
    CONF_WEEKEND_AVG_CONSUMPTION_KW,
    CONF_AWAY_AVG_CONSUMPTION_KW,
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_RATE,
    CONF_CHARGES,
    CONF_DATA_REPORT_TARGET,
    CONF_DEFAULT_FULL_CHARGE_DAY,
    CONF_ENABLE_AI_WEEKLY_INSIGHT,
    CONF_EXPORT_DISABLE_THRESHOLD,
    CONF_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
    CONF_COST_AWARE_EXPORT_SHADOW_MODE,
    CONF_IMPORT_FEEDBACK_LIVE,
    CONF_PEAK_EXPORT_LIVE,
    CONF_GENTLE_CHARGE_LIVE,
    CONF_BATTERY_VOLTAGE,
    CONF_FLUX_PRODUCTS,
    CONF_FREE_EVENT_CHARGE_RATE_KW,
    CONF_FREE_EVENT_EXPORT_RATE_KW,
    CONF_INVERTER_SERIAL,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TARGET,
    CONF_OPERATION_MODE,
    CONF_PLANT_ID,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_SOLAR_START_OFFSET_HOURS,
    CONF_HOURLY_FORECAST_SENSOR,
    CONF_HOURLY_FORECAST_ATTRIBUTE,
    CONF_OCTOPUS_IMPORT_COST_SENSOR,
    CONF_OCTOPUS_EXPORT_INCOME_SENSOR,
    CONF_OCTOPUS_GAS_COST_SENSOR,
    CONF_OCTOPUS_IMPORT_RATES_ENTITY,
    CONF_OCTOPUS_EXPORT_RATES_ENTITY,
    CONF_OCTOPUS_SAVING_SESSION_ENTITY,
    CONF_SAVING_SESSION_REWARD_PENCE,
    CONF_WEATHER_ENTITY,
    CONF_TOMORROW_FORECAST_SENSOR,
    DEFAULT_AVG_CONSUMPTION_KW,
    DEFAULT_WEEKEND_AVG_CONSUMPTION_KW,
    DEFAULT_AWAY_AVG_CONSUMPTION_KW,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGE_RATE,
    DEFAULT_ENABLE_AI_WEEKLY_INSIGHT,
    DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
    DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE,
    DEFAULT_IMPORT_FEEDBACK_LIVE,
    DEFAULT_PEAK_EXPORT_LIVE,
    DEFAULT_GENTLE_CHARGE_LIVE,
    DEFAULT_HOURLY_FORECAST_ATTRIBUTE,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SOLAR_START_OFFSET_HOURS,
    DEFAULT_TOMORROW_FORECAST_SENSOR,
    FULL_CHARGE_DAY_OPTIONS,
    GENTLE_CHARGE_SETTING,
)
from .data_logger import DAILY_COST_FIELDS, DataLogger
from .flux_helpers import (
    apply_flux_override,
    band_price_pence_per_kwh,
    build_payload,
    TARIFF_BANDS,
    merge_entry_data,
    octopus_rate_pence_per_kwh,
    price_source,
    tariff_prices_pence,
)
from .planning import (
    manual_free_event_error,
    CHEAP_WINDOW_END,
    CHEAP_WINDOW_HOURS,
    DEFAULT_BATTERY_VOLTAGE,
    LOW_SOLAR_THRESHOLD_KWH,
    STARTUP_PLAN_SOURCE,
    WEEK_HISTORY_DAYS,
    CHARGE_WATCHDOG_MINUTES,
    CHARGE_WATCHDOG_RECHECK_MINUTES,
    apply_soc_adjustments,
    charge_efficiency,
    charge_progress_ok,
    daily_report_plans,
    day_kpis,
    evening_reserve_soc,
    days_in_period,
    flux1_end_minutes,
    forecast_band,
    full_charge_day_move,
    gentle_charge_current_a,
    hhmm_to_minutes,
    import_feedback_adjustment,
    latest_complete_cost_day,
    learned_load_kw,
    max_charge_current_a,
    minutes_to_hhmm,
    net_cost_gbp,
    next_joined_saving_session,
    peak_export_plan,
    plan_free_event,
    plan_saving_session,
    resolve_load_kw,
    resolve_used_charge_rate,
    round_or_none,
    score_full_charge_day,
    select_target_soc,
    should_log_import_plan,
    solar_kwh_to_fill,
    sum_field,
    synthetic_hourly_profile,
    trailing_week,
    trim_target_soc,
    week_kpis,
    weighted_forecast_correction,
    window_grid_kwh,
)

_LOGGER = logging.getLogger(__name__)

# How many days back an Octopus "previous accumulative cost" reading is still
# accepted when its settlement is running behind schedule (see
# SunsynkOptimizer._read_octopus_previous_day_cost).
_OCTOPUS_CATCH_UP_DAYS = 5


def _octopus_oldest_date(now: datetime) -> str:
    """ISO date of the oldest day an Octopus reading is still accepted for."""
    return (now - timedelta(days=_OCTOPUS_CATCH_UP_DAYS)).date().isoformat()

_UNAVAILABLE_STATES = ("unknown", "unavailable", "none", "")
_INITIAL_REFRESH_MAX_RETRIES = 5  # 60 s apart: covers slow first poll after restart


def _api_note(api_ok: bool) -> str:
    return "" if api_ok else " (inverter NOT updated)"


def _session_api_note(api_ok: bool | None) -> str:
    """Saving-session push note: None means monitor mode (no API writes)."""
    if api_ok is None:
        return " (monitor mode — inverter not changed)"
    return _api_note(api_ok)


def _pence(value: float | None) -> str:
    return "?" if value is None else f"{round(value, 1)}p"


def _watchdog_rate_kw(plan: dict[str, Any]) -> float | None:
    """Rate the charge watchdog expects: the gentle rate on a live gentle night (phase 23)."""
    if plan.get("gentle_charge_applied") and plan.get("gentle_charge_rate_kw"):
        return plan["gentle_charge_rate_kw"]
    return plan.get("used_charge_rate_kw")


def _pct(value: float | None) -> str:
    return "?" if value is None else f"{round(value)}%"


_SAVING_SESSION_PENDING = ("scheduled", "precharging", "exporting")


def _reserve_note(trim_target: int, reserve: int) -> str:
    return " to keep the evening reserve" if trim_target == reserve else ""


class SunsynkOptimizer:
    """Implements optimizer behaviour."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.unsubs: list[Any] = []
        self._initial_refresh_attempts = 0
        self.last_trim_ts: float | None = None
        self.pending_full_trim_cancel = None
        # Snapshot of load/grid meters at the start of the 16:00-19:00 peak
        # window, used by _async_track_peak_window_usage. Not persisted: a
        # restart mid-window loses that day's snapshot (same tradeoff as
        # pending_full_trim_cancel above) — informational data only.
        self._peak_window_start: dict[str, Any] | None = None
        # Cost-aware export-disable shadow-mode tally (v1.0.11 Part 3). Counts
        # how many 30-minute checks the cost trigger disagreed with the live
        # Watt trigger, and a rough £ estimate of the exposure. Not persisted —
        # same tradeoff as _peak_window_start — read and reset weekly by
        # _async_send_weekly_cost_summary, so a restart just loses a partial
        # week's tally rather than corrupting it.
        self._shadow_export_stats: dict[str, float] = {"divergent_checks": 0, "estimated_gbp_delta": 0.0}
        # Free electricity event (phase 10) point-in-time callback handles. Not
        # persisted — restart resumption re-arms them from coordinator.state.free_event
        # via _arm_free_event_timers, same tradeoff as pending_full_trim_cancel above.
        self._free_event_unsubs: list[Any] = []
        # Saving session (phase 21) callback handles; re-armed from
        # coordinator.state.saving_session on restart, same as the free event.
        self._saving_session_unsubs: list[Any] = []
        # Gentler charging (phase 23) 05:00 restore handle; re-armed from
        # coordinator.state.gentle_charge on restart.
        self._gentle_restore_unsub = None
        # Charge watchdog (phase 22) 15-minute re-check handle. Not persisted:
        # a restart mid-check just skips that night's re-check.
        self._charge_watchdog_recheck = None
        # Today's 16:00 peak export decision (phase 17). Not persisted: after a
        # restart mid-window a trim may replace a live export window, and the
        # 22:00 bundle omits the line (the JSONL record survives).
        self._peak_export: dict[str, Any] | None = None
        self.data_logger = DataLogger(hass)

    @property
    def cfg(self) -> dict[str, Any]:
        """Return merged config (entry.data + entry.options, options win)."""
        return merge_entry_data(dict(self.entry.data), dict(self.entry.options))

    @property
    def plant_id(self) -> str:
        """Sunsynk API plant/station id used for API writes."""
        return str(self.cfg[CONF_PLANT_ID]).strip()

    @property
    def inverter_serial(self) -> str:
        """SolarSynkV3 inverter serial used in HA sensor entity ids."""
        return str(self.cfg[CONF_INVERTER_SERIAL]).strip()

    def _solarsynk_entity(self, suffix: str) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_{suffix}"

    battery_soc_entity = property(lambda self: self._solarsynk_entity("battery_soc"))
    grid_pac_entity = property(lambda self: self._solarsynk_entity("grid_pac"))
    day_pv_energy_entity = property(lambda self: self._solarsynk_entity("pv_etoday"))
    pv_mppt0_entity = property(lambda self: self._solarsynk_entity("pv_mppt0_power"))
    pv_mppt1_entity = property(lambda self: self._solarsynk_entity("pv_mppt1_power"))
    battery_temp_entity = property(lambda self: self._solarsynk_entity("battery_temperature"))
    day_load_entity = property(lambda self: self._solarsynk_entity("load_daily_used"))
    day_grid_import_entity = property(lambda self: self._solarsynk_entity("grid_etoday_from"))
    day_grid_export_entity = property(lambda self: self._solarsynk_entity("grid_etoday_to"))

    @property
    def selected_full_charge_day(self) -> str:
        """Return the active full-charge day, falling back to the config default if state is unset."""
        state_day = self.coordinator.state.selected_full_charge_day
        if state_day in FULL_CHARGE_DAY_OPTIONS:
            return state_day
        return self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY]

    @property
    def operation_mode(self) -> str:
        """Return current operation mode ('auto' or 'monitor')."""
        return str(self.cfg.get(CONF_OPERATION_MODE, DEFAULT_OPERATION_MODE))

    async def async_setup(self) -> None:
        """Create listeners."""
        daily = (
            (18, 0, self._async_choose_best_full_charge_day),  # gated to Sundays in the handler
            (1, 55, self._async_run_import_plan),
            (CHARGE_WATCHDOG_MINUTES // 60, CHARGE_WATCHDOG_MINUTES % 60, self._async_charge_watchdog),
            (6, 0, self._async_capture_morning_state),
            (22, 0, self._async_capture_day_actuals),  # also takes the 22:00 meter snapshot
            # Phase 14 KPI snapshots at the Flux band edges.
            (2, 0, self._async_meter_snapshot),
            (5, 0, self._async_meter_snapshot),
            (16, 0, self._async_meter_snapshot),
            (16, 0, self._async_peak_export),
            (19, 0, self._async_meter_snapshot),
        )
        for hour, minute, callback in daily:
            self.unsubs.append(
                async_track_time_change(self.hass, callback, hour=hour, minute=minute, second=0)
            )
        self.unsubs.append(
            async_track_time_interval(self.hass, self._async_periodic_flux2_check, timedelta(minutes=30))
        )
        self.unsubs.append(
            async_track_state_change_event(self.hass, [self.battery_soc_entity], self._async_battery_soc_changed)
        )
        saving_entity = self._cfg_str(CONF_OCTOPUS_SAVING_SESSION_ENTITY)
        if saving_entity:
            self.unsubs.append(
                async_track_state_change_event(self.hass, [saving_entity], self._async_saving_session_entity_changed)
            )

        if self.coordinator.state.selected_full_charge_day is None:
            self.coordinator.update_state(
                selected_full_charge_day=self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY]
            )

        self.coordinator.update_state(operation_mode=self.operation_mode)
        self.unsubs.append(async_call_later(self.hass, 60, self._async_initial_refresh))

        if self._free_event_active():
            self._arm_free_event_timers(self.coordinator.state.free_event)
        if self._saving_session_pending():
            self._arm_saving_session_timers(self.coordinator.state.saving_session)
        if self.coordinator.state.gentle_charge.get("phase") == "applied":
            self._arm_gentle_restore()

    async def async_shutdown(self) -> None:
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
        self._clear_free_event_timers()
        self._clear_saving_session_timers()
        if self._gentle_restore_unsub:
            self._gentle_restore_unsub()
            self._gentle_restore_unsub = None
        if self._charge_watchdog_recheck:
            self._charge_watchdog_recheck()
            self._charge_watchdog_recheck = None
        if self.pending_full_trim_cancel:
            self.pending_full_trim_cancel()
            self.pending_full_trim_cancel = None

    def _available_state(self, entity_id: str):
        """Return the entity's State object, or None if missing/unavailable/unknown."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE_STATES:
            return None
        return state

    def _essential_state(self, entity_id: str) -> float | None:
        """Numeric state of an entity, or None when missing/unavailable/unparseable.

        Use for inputs whose absence should abort the cycle (battery_soc, forecast)
        rather than silently defaulting to 0 and producing a worst-case action.
        """
        state = self._available_state(entity_id)
        if state is None:
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _state_float(self, entity_id: str, default: float = 0.0) -> float:
        """Numeric state of an entity, or `default` if unavailable/unparseable."""
        value = self._essential_state(entity_id)
        return default if value is None else value

    def _cfg_str(self, key: str, cfg: dict[str, Any] | None = None) -> str:
        """Stripped string config value ('' when unset) — blank means feature disabled."""
        return str((cfg if cfg is not None else self.cfg).get(key, "")).strip()

    def _sun_times(self) -> tuple[Any, Any] | None:
        """Return (next_rising, next_setting) as local datetimes from sun.sun, or None."""
        sun_state = self.hass.states.get("sun.sun")
        if not sun_state:
            return None
        rising_str = sun_state.attributes.get("next_rising")
        setting_str = sun_state.attributes.get("next_setting")
        rising = dt_util.parse_datetime(rising_str) if rising_str else None
        setting = dt_util.parse_datetime(setting_str) if setting_str else None
        return (
            dt_util.as_local(rising) if rising else None,
            dt_util.as_local(setting) if setting else None,
        )

    def _cooldown_ok(self, seconds: int = 1800) -> bool:
        """Return True if at least `seconds` have elapsed since the last trim action."""
        if self.last_trim_ts is None:
            return True
        return (dt_util.utcnow().timestamp() - self.last_trim_ts) > seconds

    def _mark_trim(self) -> None:
        """Record the current time as the last trim timestamp for cooldown tracking."""
        self.last_trim_ts = dt_util.utcnow().timestamp()

    async def async_notify(self, title: str, message: str, target: str | None = None) -> None:
        """Send a notification via the configured HA notify service."""
        cfg = self.cfg
        service_string = self._cfg_str(CONF_NOTIFY_SERVICE, cfg)
        record: dict[str, Any] = {"ok": False, "service": service_string, "title": title, "message": message}
        if "." not in service_string:
            self.coordinator.update_state(
                last_error=f"Invalid notify service: {service_string}",
                last_notification=record,
            )
            return

        domain, service = service_string.split(".", 1)
        data: dict[str, Any] = {"title": title, "message": message}
        notify_target = target or self._cfg_str(CONF_NOTIFY_TARGET, cfg)
        if notify_target:
            data["target"] = [notify_target]
        record["target"] = notify_target or None

        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Notification failed")
            self.coordinator.update_state(
                last_error=f"Notification failed: {exc}",
                last_notification={**record, "error": str(exc)},
            )
            return
        self.coordinator.update_state(last_notification={**record, "ok": True})

    async def _async_post_with_status(self, payload: dict[str, Any]) -> bool:
        """POST to the API and surface success/failure via coordinator state.

        Returns True on success, False on failure. On failure the exception is
        logged, last_error is populated, and last_api_result records the error
        so callers and the dashboard see that the push did NOT apply.
        """
        try:
            result = await self.coordinator.api.async_post_income(self.plant_id, payload)
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Sunsynk API push failed")
            self.coordinator.update_state(
                last_api_result={"ok": False, "error": str(exc)},
                last_error=f"API push failed: {exc}",
            )
            return False
        self.coordinator.update_state(
            last_api_result={"ok": True, **(result if isinstance(result, dict) else {"result": result})},
        )
        return True

    async def async_push_current_config(self) -> bool:
        """Push the baseline Flux config from settings to the Sunsynk API without any overrides."""
        return await self._async_post_with_status(build_payload(self.cfg))

    async def async_push_flux_override(self, payload: dict[str, Any]) -> bool:
        """Merge a Flux 1/2 override dict onto the config baseline and push to the API.

        Returns True on success, False on API failure — callers should reflect this
        in their notification text so the user knows whether the inverter received
        the new config.
        """
        config = self.cfg
        flux_products = apply_flux_override(
            config.get(CONF_FLUX_PRODUCTS, []),
            payload.get("flux_1"),
            payload.get("flux_2"),
        )
        return await self._async_post_with_status(build_payload(config, flux_products))

    async def async_reset_flux_baseline(self) -> None:
        config = self.cfg
        payload = build_payload(config, config.get(CONF_FLUX_PRODUCTS, []))
        ok = await self._async_post_with_status(payload)
        self.coordinator.update_state(
            last_flux2_action={
                "action": "reset_baseline",
                "payload": payload,
                "notified": True,
                "source": "user_button",
                "reason": "manual_reset",
                "api_ok": ok,
            },
            evening_export_disabled=False,
        )
        title = "🔋 Sunsynk: baseline restored" if ok else "⚠️ Sunsynk: baseline NOT restored"
        body = (
            "Inverter Flux windows returned to your configured baseline. Evening export re-enabled."
            if ok
            else "API push failed — the inverter still has the previous settings. Try again or check Last error."
        )
        await self.async_notify(title, body)

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

    async def _async_weekday_weather_scores(self) -> dict[str, float | None] | None:
        """Mon–Fri full-charge scores from the daily weather forecast.

        A weekday missing from the forecast scores None; None overall (with
        last_error set) when the weather service call fails.
        """
        weather_entity = self.cfg[CONF_WEATHER_ENTITY]
        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "daily"},
                blocking=True,
                return_response=True,
            )
        except Exception as exc:  # pragma: no cover
            self.coordinator.update_state(last_error=f"Weather forecast failed: {exc}")
            return None

        forecast_items = []
        if isinstance(response, dict):
            weather_data = response.get(weather_entity)
            if isinstance(weather_data, dict):
                forecast_items = weather_data.get("forecast", []) or []

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

        battery_capacity_kwh = max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        if self.coordinator.state.away_mode:
            avg_consumption_kw = float(cfg.get(CONF_AWAY_AVG_CONSUMPTION_KW, DEFAULT_AWAY_AVG_CONSUMPTION_KW))
        else:
            avg_consumption_kw = float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))
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

    async def async_run_import_plan(self, source: str = "automatic", dry_run: bool = False) -> None:
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

        # Pre-flight: battery SOC is essential. If missing we cannot compute
        # energy_needed = (target - soc), so skip rather than default soc=0
        # which would size a full-window max-import.
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            await self._async_skip_plan(
                f"Battery SOC entity {self.battery_soc_entity} unavailable — import plan skipped"
            )
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

        battery_capacity_kwh = max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        charge_rate_kw = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        # Occupancy regime for this plan: home vs away (holiday). Drain and the
        # evening-SOC nudge are learned per-regime so a low-load holiday can't skew
        # the home profile; forecast correction stays global (load-independent).
        away = bool(self.coordinator.state.away_mode)
        is_weekend = today in ("Saturday", "Sunday")
        # Away takes precedence over weekday/weekend: on holiday the house load is
        # low regardless of the day, so the solar bridge sizes to the lower figure.
        if away:
            avg_consumption_kw = float(cfg.get(CONF_AWAY_AVG_CONSUMPTION_KW, DEFAULT_AWAY_AVG_CONSUMPTION_KW))
        elif is_weekend:
            avg_consumption_kw = float(cfg.get(CONF_WEEKEND_AVG_CONSUMPTION_KW, DEFAULT_WEEKEND_AVG_CONSUMPTION_KW))
        else:
            avg_consumption_kw = float(cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))

        paired_days = await self.data_logger.async_load_paired_days(days=30)
        # Phase 12: home nights use the overnight rate learned from the 06:00
        # load readings (clamped to 0.5x-2x config); away keeps its config rate.
        learned_kw, learned_load_days = (None, 0) if away else learned_load_kw(paired_days, now.date(), is_weekend)
        avg_consumption_kw, load_source = resolve_load_kw(avg_consumption_kw, learned_kw)
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
        # Phase 21: a saving session later today worth filling for → charge to 100%.
        saving_session_boost = self._saving_session_boost_today(now)
        if saving_session_boost:
            target_soc = 100

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
        used_charge_rate, temp_factor = resolve_used_charge_rate(
            charge_rate_kw, effective_charge_rate, battery_temp_c
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
        flux1_end = minutes_to_hhmm(end_minutes)
        # Phase 23: the lowest grid-charge current that still reaches the target
        # by 05:00. Logged only unless the live option is on; then the current
        # is written first and the window stays open to 05:00.
        battery_voltage = float(cfg.get(CONF_BATTERY_VOLTAGE) or DEFAULT_BATTERY_VOLTAGE)
        gentle_max_a = max_charge_current_a(charge_rate_kw, battery_voltage)
        gentle_current_a = gentle_charge_current_a(energy_needed_kwh, CHEAP_WINDOW_HOURS, battery_voltage, gentle_max_a)
        gentle_charge_applied = False
        if not dry_run and self._gentle_charge_due(cfg, now, gentle_current_a, gentle_max_a):
            gentle_charge_applied = await self._async_apply_gentle_charge(gentle_current_a, now)
            if gentle_charge_applied:
                end_minutes = hhmm_to_minutes(CHEAP_WINDOW_END)
                flux1_end = CHEAP_WINDOW_END
        window_load_kwh, grid_kwh_needed = window_grid_kwh(
            energy_needed_kwh, efficiency, avg_consumption_kw, end_minutes
        )
        next_import_window = f"02:00→{flux1_end}"

        payload = {
            "flux_1": {"startTime": "02:00", "endTime": flux1_end, "targetSoc": target_soc},
            "flux_2": {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85},
        }
        # A free event or saving session holds Flux 1/2 for its own windows; the
        # plan is still computed and logged so it's ready to push the moment the
        # event ends (_async_restore_normal_plan reads it back).
        held_by = None if dry_run else self._slots_held_by()
        api_ok = None if (dry_run or held_by) else await self.async_push_flux_override(payload)

        plan_state = {
            "date": now.date().isoformat(),
            "today": today,
            "selected_full_charge_day": full_day,
            "is_full_day": is_full_day,
            "soc": soc,
            "raw_forecast_kwh": raw_forecast_kwh,
            "forecast_correction_factor": forecast_correction,
            "forecast_correction_weighted": weighted_correction,
            "forecast_correction_weighted_basis": weighted_basis,
            "solar_forecast_kwh": solar_forecast_kwh,
            "low_solar_forecast_kwh": round(low_solar_forecast_kwh, 2),
            "forecast_band": band,
            "logic_branch": logic_branch,
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
            "held_by_free_event": held_by == "free_event",
            "held_by_saving_session": held_by == "saving_session",
            "saving_session_boost": saving_session_boost,
            "battery_voltage": battery_voltage,
            "gentle_current_a": gentle_current_a,
            "gentle_max_current_a": round(gentle_max_a, 1),
            "gentle_charge_rate_kw": (
                round(gentle_current_a * battery_voltage / 1000, 2) if gentle_current_a is not None else None
            ),
            "gentle_charge_applied": gentle_charge_applied,
            "forecast_fallback": forecast_fallback,
            "is_weekend": is_weekend,
            "away": away,
            "avg_consumption_kw": avg_consumption_kw,
            "load_source": load_source,
            "learned_load_kw": learned_kw,
            "learned_load_days": learned_load_days,
            "battery_temp_c": battery_temp_c,
            "temp_deration_factor": temp_factor,
            "hourly_forecast_used": decision.hourly_forecast_used,
            "synthetic_ramp": decision.synthetic_ramp,
            "bridge_hour": decision.bridge_hour,
        }

        if dry_run:
            if self._saving_session_pending():
                plan_state["saving_session"] = dict(self.coordinator.state.saving_session)
            await self.async_notify(
                f"🧪 Sunsynk: test plan (dry run) — {plan_state['date']}",
                json.dumps(plan_state),
            )
            return

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
        await self._async_notify_import_plan(plan_state)

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
        elif plan.get("held_by_saving_session"):
            title = "🔋 Sunsynk: import plan computed (held for saving session)"
            api_note = " A saving session holds the Flux slots; this plan pushes when it ends."
        else:
            title = "🔋 Sunsynk: import plan set" if api_ok else "⚠️ Sunsynk: import plan NOT applied"
            api_note = "" if api_ok else " Inverter NOT updated; will retry next cycle."
        full_day_note = " — full-charge day" if plan["is_full_day"] else ""
        if plan.get("saving_session_boost"):
            full_day_note += " — saving session today"
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
            ),
        )

    async def async_run_flux2_check(self, source: str = "automatic") -> None:
        """Run Flux 2 evening export / trim logic."""
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
                {"startTime": "16:00", "endTime": "16:15", "targetSoc": 100},
                base_action,
                reason,
                evening_export_disabled=True,
            )
            title = "🔋 Sunsynk: evening export paused" if api_ok else "⚠️ Sunsynk: export pause NOT applied"
            await self.async_notify(
                title,
                (
                    f"{trigger_desc}. "
                    f"Export paused by holding target SOC at 100%; it re-enables automatically.{_api_note(api_ok)}"
                ),
            )
            return

        # Trim if SOC exceeds 85% on a non-full-charge day. Target 82% leaves a 3% gap
        # below the trigger so normal fluctuation doesn't immediately re-trigger a trim.
        # Phase 16: never below the evening reserve (19:00 → 02:00 load).
        reserve = self._evening_reserve_soc()
        trim_target = trim_target_soc(soc, reserve)
        if (
            not is_full_day and soc > 85 and trim_target is not None
            and not self._peak_export_holds_flux2(now_local) and self._cooldown_ok()
        ):
            self._mark_trim()
            api_ok = await self._async_push_flux2_action(
                "trim_to_82",
                {
                    "startTime": now_local.strftime("%H:%M"),
                    "endTime": (now_local + timedelta(minutes=45)).strftime("%H:%M"),
                    "targetSoc": trim_target,
                },
                {**base_action, "evening_reserve_soc": reserve},
                f"soc_{round(soc)}%_exceeds_85",
            )
            title = "🔋 Sunsynk: battery trim" if api_ok else "⚠️ Sunsynk: battery trim NOT applied"
            await self.async_notify(
                title,
                f"SOC {round(soc, 1)}% is above 85%. Trimming to {trim_target}%"
                f"{_reserve_note(trim_target, reserve)}.{_api_note(api_ok)}",
            )
            return

        self.coordinator.update_state(
            last_flux2_action={
                "action": "none",
                **base_action,
                "notified": False,
                "reason": "no_trigger",
            },
            evening_export_disabled=False,
            operation_mode=self.operation_mode,
        )

    async def _async_push_flux2_action(
        self,
        action: str,
        flux_2: dict[str, Any],
        base_action: dict[str, Any],
        reason: str,
        evening_export_disabled: bool = False,
    ) -> bool:
        """Push a Flux 2 override and record it as last_flux2_action. Returns api_ok."""
        payload = {"flux_2": flux_2}
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

    async def _guarded(self, coro_factory, label: str) -> None:
        """Run a scheduled coroutine, surfacing any exception via last_error.

        Scheduled time-change/interval callbacks otherwise let an exception
        (e.g. a misconfigured charge rate causing ZeroDivisionError, or a
        missing config key) escape into the HA event loop with no plan pushed,
        no notification, and nothing on the dashboard — a silent failure.
        """
        try:
            await coro_factory()
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("%s failed", label)
            self.coordinator.update_state(last_error=f"{label} failed: {exc}")

    async def _async_initial_refresh(self, _now) -> None:
        """Populate initial state soon after startup.

        Skips the one-shot plan if a reload lands mid-evening while export is
        paused: the plan re-pushes flux_2 to its default (targetSoc 85) and
        would re-enable export at the worst moment. The periodic Flux 2 check
        keeps the pause and the nightly 01:55 plan re-plans normally.
        """
        now = dt_util.now()
        if self._slots_held():
            _LOGGER.info("Skipping initial import plan — %s holds the Flux slots", self._slots_held_by())
            return
        if self.coordinator.state.evening_export_disabled and 16 <= now.hour < 19:
            _LOGGER.info("Skipping initial import plan — evening export pause active")
            return
        if (
            self._essential_state(self.battery_soc_entity) is None
            and self._initial_refresh_attempts < _INITIAL_REFRESH_MAX_RETRIES
        ):
            # Sensors haven't polled yet after a restart/reload: wait rather than
            # send a spurious "plan skipped" warning.
            self._initial_refresh_attempts += 1
            _LOGGER.info("Initial import plan deferred — battery SOC not yet available")
            self.unsubs.append(async_call_later(self.hass, 60, self._async_initial_refresh))
            return
        await self._guarded(
            lambda: self.async_run_import_plan(source=STARTUP_PLAN_SOURCE), "Initial refresh"
        )

    async def _async_choose_best_full_charge_day(self, _now) -> None:
        """Time-change callback at 18:00 daily: Sunday weekly pick, then the daily re-check."""
        if dt_util.now().strftime("%A") == "Sunday":
            await self._guarded(self.async_choose_best_full_charge_day, "Full-charge-day selection")
            await self._guarded(self._async_send_weekly_cost_summary, "Weekly cost summary")
            await self._guarded(self._async_send_ai_weekly_insight, "AI weekly insight")
        await self._guarded(self.async_recheck_full_charge_day, "Full-charge-day re-check")

    async def _async_send_weekly_cost_summary(self) -> None:
        """Sunday 18:00: roll up the 7 complete days ending yesterday (Sun–Sat) and send it as JSON.

        Reuses the existing data_report_target debug stream rather than adding
        a new notify target — this is a second machine-readable line, not a
        human-facing message. Days without a daily_cost record (Octopus
        unconfigured/unavailable that day) simply don't contribute to the
        sums, same graceful-degrade shape as the rest of the Octopus link.
        """
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if not data_report_target:
            return

        # Read and reset the cost-aware export-disable shadow-mode tally
        # (v1.0.11 Part 3) — how many 30-minute checks this week the cost
        # trigger disagreed with the live Watt trigger, and the rough £
        # exposure estimate. Reset here so each week's digest reports only
        # that week's divergence, not a running total.
        shadow_mode_tally = dict(self._shadow_export_stats)
        shadow_mode_tally["shadow_mode"] = bool(
            self.cfg.get(CONF_COST_AWARE_EXPORT_SHADOW_MODE, DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE)
        )
        self._shadow_export_stats = {"divergent_checks": 0, "estimated_gbp_delta": 0.0}

        period_start, period_end = trailing_week(dt_util.now().date())
        paired_days = days_in_period(
            await self.data_logger.async_load_paired_days(days=WEEK_HISTORY_DAYS), period_start, period_end
        )
        cost_days = [d for d in paired_days if d.get("net_cost_gbp") is not None]

        def _sum(key: str) -> float | None:
            return sum_field(paired_days, key)

        summary = {
            "type": "weekly_cost_summary",
            "date": dt_util.now().date().isoformat(),
            "period_start": period_start,
            "period_end": period_end,
            "days_in_period": len(paired_days),
            "days_with_cost_data": len(cost_days),
            "week_import_cost_gbp": _sum("actual_import_cost_gbp"),
            "week_export_income_gbp": _sum("actual_export_income_gbp"),
            "week_gas_cost_gbp": _sum("actual_gas_cost_gbp"),
            "week_net_cost_gbp": sum_field(cost_days, "net_cost_gbp"),
            "week_load_kwh": _sum("day_load_kwh"),
            "week_solar_kwh": _sum("actual_solar_kwh"),
            "year_to_date": self.coordinator.state.last_year_to_date_cost or {},
            "cost_aware_export_shadow_tally": shadow_mode_tally,
            **week_kpis(paired_days),
            # Phase 17: shadow (or live) peak-export gain over the week.
            "week_peak_export_kwh": _sum("peak_export_kwh"),
            "week_peak_export_gain_gbp": _sum("peak_export_gain_gbp"),
            "peak_export_live": bool(self.cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE)),
        }


        await self.async_notify(
            "Sunsynk Weekly Cost Summary",
            json.dumps(summary),
            target=data_report_target,
        )

    async def _async_send_ai_weekly_insight(self) -> None:
        """Sunday 18:00: ask HA's AI Task feature for a plain-English weekly insight.

        Off by default (CONF_ENABLE_AI_WEEKLY_INSIGHT) since it depends on the
        user having an AI Task provider configured in this HA instance and may
        incur cost on their own configured LLM. Graceful-degrade, same
        principle as every other optional integration in this component: if
        the toggle is off, or no ai_task service/entity is actually available
        in this HA instance, this silently no-ops rather than erroring — an
        unconfigured AI Task provider isn't a failure, just an unused optional
        feature. Sends its own human-readable notification via the main
        notify target (CONF_NOTIFY_TARGET), separate from the JSON debug
        stream used by the weekly cost summary above — a generated narrative
        doesn't belong alongside machine-readable lines.
        """
        if not bool(self.cfg.get(CONF_ENABLE_AI_WEEKLY_INSIGHT, DEFAULT_ENABLE_AI_WEEKLY_INSIGHT)):
            return
        if not self.hass.services.has_service("ai_task", "generate_data"):
            return
        if not self.hass.states.async_entity_ids("ai_task"):
            return

        charges = self.cfg.get(CONF_CHARGES, [])
        def _bands(status: str) -> str:
            return ", ".join(
                f"{row['price']}p {row['startRange']}-{row['endRange']}"
                for row in charges
                if row.get("status") == status
            )

        import_bands = _bands("import")
        export_bands = _bands("export")

        paired_days = days_in_period(
            await self.data_logger.async_load_paired_days(days=WEEK_HISTORY_DAYS),
            *trailing_week(dt_util.now().date()),
        )

        def _sum(key: str):
            total = sum_field(paired_days, key)
            return "unknown" if total is None else total

        ytd = self.coordinator.state.last_year_to_date_cost or {}

        context = (
            f"Tariff (Octopus Flux) import p/kWh by window: {import_bands or 'unknown'}. "
            f"Export p/kWh by window: {export_bands or 'unknown'}. "
            f"Last 7 complete days: import cost £{_sum('actual_import_cost_gbp')}, "
            f"export income £{_sum('actual_export_income_gbp')}, "
            f"gas cost £{_sum('actual_gas_cost_gbp')}, "
            f"household load {_sum('day_load_kwh')} kWh, "
            f"solar generated {_sum('actual_solar_kwh')} kWh, "
            f"solar forecast {_sum('solar_forecast_kwh')} kWh. "
            f"Year-to-date net cost: £{ytd.get('net_cost_gbp', 'unknown')} over {ytd.get('days_counted', 0)} days."
        )
        instructions = (
            "You are reviewing one week of data for a home battery/solar optimiser on the Octopus "
            "Flux tariff. The goal is the lowest possible combined annual electricity + gas bill. "
            "Given the tariff price bands and this week's actual costs/loads/solar below, write a "
            "short (3-5 sentence) plain-English insight: note anything notable (e.g. cost trending "
            "up or down, solar underperforming forecast, high load during an expensive window), and "
            "suggest at most one concrete config change if one is clearly justified — otherwise say "
            f"things look on track. Data: {context}"
        )

        response = await self.hass.services.async_call(
            "ai_task",
            "generate_data",
            {"task_name": "Sunsynk weekly insight", "instructions": instructions},
            blocking=True,
            return_response=True,
        )
        insight = (response or {}).get("data")
        if not insight:
            return
        await self.async_notify("🔋 Sunsynk: weekly insight", str(insight))

    async def _async_run_import_plan(self, _now) -> None:
        """Time-change callback at 01:55 daily."""
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
        capacity = max(0.1, float(self.cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
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
        now = dt_util.now()
        plan = self._charge_watchdog_plan(now)
        if plan is None:
            return
        capacity = max(0.1, float(self.cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
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
            "Check the inverter's time-of-use settings.",
        )

    async def _async_periodic_flux2_check(self, _now) -> None:
        """30-minute interval callback."""
        await self._guarded(self.async_run_flux2_check, "Periodic Flux 2 check")
        await self._guarded(self._async_track_peak_window_usage, "Peak-window usage tracking")
        await self._guarded(self.async_check_octopus_saving_session, "Saving session auto-detect")
        await self._guarded(self.async_restore_charge_current, "Grid charge current restore")

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

    async def _async_meter_snapshot(self, now) -> None:
        time = dt_util.as_local(now).strftime("%H:%M")
        await self._guarded(lambda: self.async_capture_meter_snapshot(time), "Meter snapshot")

    async def async_capture_meter_snapshot(self, time: str) -> None:
        """Log the cumulative daily meters at a Flux band edge (phase 14).

        Read-only, so it also runs in monitor mode. Unavailable meters log
        None, so the KPIs that need them come out None, never 0.
        """
        await self.data_logger.async_log_meter_snapshot(
            date=dt_util.now().date().isoformat(),
            time=time,
            grid_import_kwh=round_or_none(self._essential_state(self.day_grid_import_entity)),
            grid_export_kwh=round_or_none(self._essential_state(self.day_grid_export_entity)),
            load_kwh=round_or_none(self._essential_state(self.day_load_entity)),
            soc=round_or_none(self._essential_state(self.battery_soc_entity), 1),
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
        kpis = day_kpis(
            snapshots,
            prices,
            max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY))),
            late_load_kw,
        )
        record = {
            "date": date,
            "snapshot_times": sorted(snapshots),
            **kpis,
            "price_source": price_source(sources, *TARIFF_BANDS),
            "evening_reserve_soc": self._evening_reserve_soc(),
        }
        await self.data_logger.async_log_day_kpis(**record)
        return {"type": "day_kpis", **record}

    async def _async_peak_export(self, _now) -> None:
        await self._guarded(self.async_run_peak_export, "Peak surplus export")

    async def async_run_peak_export(self) -> None:
        """16:00: sell the SOC above the evening reserve + margin at the peak rate (phase 17).

        Shadow by default: the decision, kWh and £ are logged and nothing is
        pushed. Live (CONF_PEAK_EXPORT_LIVE) pushes Flux 2 16:00-19:00 to the
        target via _async_post_with_status. Skipped while a free event or
        saving session holds the slots; never pushes in monitor mode; the export-disable (watt
        trigger) wins — if it already fired, or fires later, it holds 100%.
        """
        if self._slots_held():
            return
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            return
        cfg = self.cfg
        now = dt_util.now()
        reserve = self._evening_reserve_soc()
        prices, sources = self._tariff_prices(now.date())
        plan = peak_export_plan(
            soc,
            reserve,
            max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY))),
            prices["export_peak"],
            prices["offpeak"],
        )
        if plan["decision"] == "export" and self.coordinator.state.evening_export_disabled:
            plan["decision"] = "export_disabled"
        live = bool(cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE)) and self.operation_mode != "monitor"
        api_ok = None
        if live and plan["decision"] == "export":
            api_ok = await self._async_push_flux2_action(
                "peak_export",
                {"startTime": "16:00", "endTime": "19:00", "targetSoc": plan["target_soc"]},
                {"soc": soc, "source": "automatic", "evening_reserve_soc": reserve},
                f"surplus_{plan['export_kwh']}kWh_above_{plan['target_soc']}%",
            )
            title = "🔋 Sunsynk: selling surplus at peak rate" if api_ok else "⚠️ Sunsynk: peak export NOT applied"
            await self.async_notify(
                title,
                f"SOC {round(soc, 1)}%: exporting about {plan['export_kwh']} kWh down to "
                f"{plan['target_soc']}% (evening reserve {reserve}%) until 19:00.{_api_note(api_ok)}",
            )
        self._peak_export = {
            "type": "peak_export",
            "date": now.date().isoformat(),
            "soc": soc,
            "evening_reserve_soc": reserve,
            **plan,
            "price_source": price_source(sources, "export_peak", "offpeak"),
            "live": live,
            "api_ok": api_ok,
        }
        await self.data_logger.async_log_peak_export(
            **{k: v for k, v in self._peak_export.items() if k != "type"}
        )

    def _tariff_prices(self, on_date=None) -> tuple[dict[str, float | None], dict[str, str | None]]:
        """Band prices (p/kWh) + per-band source: Octopus rate entities first, then `charges` (phase 18)."""
        cfg = self.cfg
        entities = {
            "import": self._cfg_str(CONF_OCTOPUS_IMPORT_RATES_ENTITY, cfg),
            "export": self._cfg_str(CONF_OCTOPUS_EXPORT_RATES_ENTITY, cfg),
        }
        octopus: dict[str, float | None] = {}
        for key, (start, end, status) in TARIFF_BANDS.items():
            state = self.hass.states.get(entities[status]) if entities[status] else None
            if state is not None:
                octopus[key] = octopus_rate_pence_per_kwh(
                    state.state, dict(state.attributes), start, end, on_date, dt_util.DEFAULT_TIME_ZONE
                )
        return tariff_prices_pence(cfg.get(CONF_CHARGES, []), octopus)

    def _peak_export_holds_flux2(self, now_local) -> bool:
        """True while today's live peak export window owns Flux 2 (16:00-19:00)."""
        rec = self._peak_export or {}
        return (
            rec.get("api_ok") is True
            and rec.get("date") == now_local.date().isoformat()
            and 16 <= now_local.hour < 19
        )

    def _evening_reserve_soc(self) -> int:
        """Phase 16: SOC to hold at 19:00 so the house runs to the 02:00 off-peak start.

        Load is tonight's plan rate (phase 12 learned or config), else config.
        """
        cfg = self.cfg
        load_kw = (self.coordinator.state.last_import_plan or {}).get("avg_consumption_kw")
        if load_kw is None:
            load_kw = cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW)
        capacity = max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        return evening_reserve_soc(float(load_kw), capacity)

    def _read_daily_meters(self) -> dict[str, float]:
        """SolarSynkV3 cumulative daily load / grid import / grid export (kWh)."""
        return {
            "load": self._state_float(self.day_load_entity, 0),
            "grid_import": self._state_float(self.day_grid_import_entity, 0),
            "grid_export": self._state_float(self.day_grid_export_entity, 0),
        }

    async def _async_battery_soc_changed(self, event: Event) -> None:
        """Handle SOC threshold-based reactions."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
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
                self.pending_full_trim_cancel = None
                current_soc = self._state_float(self.battery_soc_entity, 0)
                if current_soc < 99.5:
                    return
                # Phase 16: floor the sell-down at the evening reserve.
                reserve = self._evening_reserve_soc()
                trim_target = trim_target_soc(current_soc, reserve)
                now_local = dt_util.now()
                # A live peak export (phase 17) already sells lower; don't replace it.
                if trim_target is None or self._peak_export_holds_flux2(now_local):
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

    async def _async_capture_morning_state(self, _now) -> None:
        """Capture SOC and PV power at 06:00 to measure overnight battery drain.

        Also captures the SolarSynkV3 daily load total, which at 06:00 equals
        real 00:00-06:00 household consumption — a direct energy figure to
        cross-check against the SOC-derived overnight_drain_pct.
        """
        soc = self._state_float(self.battery_soc_entity, 0)
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
                "morning_soc": round(soc, 1),
                "morning_pv_power": round(pv_power, 1),
                "overnight_load_kwh": round_or_none(overnight_load_kwh),
            },
        )
        await self._async_capture_daily_cost()

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

    async def _async_capture_day_actuals(self, _now) -> None:
        """Capture end-of-day actuals at 22:00 and log them.

        Includes the SolarSynkV3 daily load/grid totals alongside SOC and solar,
        so consumption analysis doesn't have to infer household load from the
        SOC swing (which conflates load with solar availability) — the actual
        daily load figure is logged directly.
        """
        await self._async_capture_daily_cost()
        soc = self._state_float(self.battery_soc_entity, 0)
        actual_solar_kwh = self._state_float(self.day_pv_energy_entity, 0)
        # Unavailable meters log None, not 0.0 — a zero load would skew learning.
        day_load_kwh = self._essential_state(self.day_load_entity)
        day_grid_import_kwh = self._essential_state(self.day_grid_import_entity)
        day_grid_export_kwh = self._essential_state(self.day_grid_export_entity)
        now = dt_util.now()
        date = now.date().isoformat()
        evening_export_disabled = self.coordinator.state.evening_export_disabled
        await self.data_logger.async_log_day_actuals(
            date=date,
            evening_soc=soc,
            actual_solar_kwh=actual_solar_kwh,
            evening_export_disabled=evening_export_disabled,
            day_load_kwh=day_load_kwh,
            day_grid_import_kwh=day_grid_import_kwh,
            day_grid_export_kwh=day_grid_export_kwh,
        )
        actuals_rec = {
            "type": "day_actuals",
            "date": date,
            "evening_soc": round(soc, 1),
            "actual_solar_kwh": round(actual_solar_kwh, 2),
            "evening_export_disabled": evening_export_disabled,
            "day_load_kwh": round_or_none(day_load_kwh),
            "day_grid_import_kwh": round_or_none(day_grid_import_kwh),
            "day_grid_export_kwh": round_or_none(day_grid_export_kwh),
        }
        kpi_rec = await self._async_capture_day_kpis(date)
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
            peak_export_rec = self._peak_export if (self._peak_export or {}).get("date") == date else {}
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
                for r in [*plan_recs, morning_rec, actuals_rec, kpi_rec, peak_rec, peak_export_rec, cost_rec, complete_rec]
                if r
            )
            await self.async_notify(
                f"Sunsynk Daily Data — {date}",
                lines,
                target=data_report_target,
            )

    # ------------------------------------------------------------------ #
    # Free electricity event (phase 10 — manual entry)                     #
    # ------------------------------------------------------------------ #

    def _free_event_active(self) -> bool:
        """True while a free event holds the Flux slots (scheduled/selling/charging)."""
        return self.coordinator.state.free_event.get("phase") in ("scheduled", "selling", "charging")

    def _clear_free_event_timers(self) -> None:
        for unsub in self._free_event_unsubs:
            unsub()
        self._free_event_unsubs = []

    def _arm_free_event_timers(self, event: dict[str, Any]) -> None:
        """(Re-)arm the point-in-time callbacks for a scheduled/in-progress free event.

        Called right after scheduling and on startup to resume an event that
        survived an HA restart — each callback fires immediately (0-second
        delay) if its trigger time has already passed.
        """
        self._clear_free_event_timers()
        now = dt_util.now()
        phase = event.get("phase")
        free_start = dt_util.as_local(dt_util.parse_datetime(event["free_start"]))
        free_end = dt_util.as_local(dt_util.parse_datetime(event["free_end"]))
        sell_start = dt_util.as_local(dt_util.parse_datetime(event["sell_start"]))

        if phase == "scheduled" and not event.get("skip_sell"):
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, (sell_start - now).total_seconds()), self._async_free_event_sell_start)
            )
        if phase in ("scheduled", "selling"):
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, (free_start - now).total_seconds()), self._async_free_event_free_start)
            )
        if phase in ("scheduled", "selling", "charging"):
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, (free_end - now).total_seconds()), self._async_free_event_free_end)
            )

    async def async_try_schedule_manual_free_event(self, changed: str = "end") -> None:
        """Called after either manual start/end datetime entity is set (``changed`` names which).

        Schedules the event once both are present and valid; otherwise waits
        for the other one (or, if invalid, notifies and leaves both as-is for
        the user to correct).
        """
        state = self.coordinator.state
        if not state.free_event_manual_start or not state.free_event_manual_end:
            return
        free_start = dt_util.as_local(dt_util.parse_datetime(state.free_event_manual_start))
        free_end = dt_util.as_local(dt_util.parse_datetime(state.free_event_manual_end))
        error = manual_free_event_error(changed, free_start, free_end, dt_util.now())
        if error:
            await self.async_notify("⚠️ Sunsynk: free event NOT scheduled", error)
            return
        if free_end <= free_start or free_start <= dt_util.now():
            return  # stale counterpart: wait for the other field to be set
        await self.async_schedule_free_event(free_start, free_end, source="manual")

    async def async_schedule_free_event(self, free_start: datetime, free_end: datetime, source: str) -> bool:
        """Plan and arm a free-electricity event. Returns True if it was scheduled."""
        if self._free_event_active() or self._saving_session_pending():
            await self.async_notify(
                "⚠️ Sunsynk: free event NOT scheduled",
                "A free event or saving session is already scheduled or in progress. Cancel it first.",
            )
            return False

        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            await self.async_notify(
                "⚠️ Sunsynk: free event NOT scheduled",
                f"Battery SOC entity {self.battery_soc_entity} unavailable.",
            )
            return False

        cfg = self.cfg
        battery_capacity_kwh = max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        default_rate = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        charge_rate_kw = float(cfg.get(CONF_FREE_EVENT_CHARGE_RATE_KW) or default_rate)
        export_rate_kw = float(cfg.get(CONF_FREE_EVENT_EXPORT_RATE_KW) or default_rate)

        result = plan_free_event(free_start, free_end, soc, battery_capacity_kwh, charge_rate_kw, export_rate_kw)

        event = {
            "source": source,
            "free_start": free_start.isoformat(),
            "free_end": free_end.isoformat(),
            "sell_start": result.sell_start.isoformat(),
            "sell_end": result.sell_end.isoformat(),
            "sell_floor_soc": result.sell_floor_soc,
            "expected_export_kwh": result.expected_export_kwh,
            "expected_refill_kwh": result.expected_refill_kwh,
            "skip_sell": result.skip_sell,
            "charge_rate_kw": charge_rate_kw,
            "export_rate_kw": export_rate_kw,
            "phase": "scheduled",
        }
        self.coordinator.update_state(free_event=event)
        self._arm_free_event_timers(event)

        skip_note = " SOC is already at/below the floor, so the sell is skipped." if result.skip_sell else ""
        await self.async_notify(
            "🔋 Sunsynk: free event scheduled",
            (
                f"Free period {free_start.strftime('%Y-%m-%d %H:%M')} → {free_end.strftime('%H:%M')} ({source}). "
                f"Sell floor {result.sell_floor_soc}%, expected export {result.expected_export_kwh} kWh, "
                f"expected refill {result.expected_refill_kwh} kWh.{skip_note}"
            ),
        )
        await self.data_logger.async_log_free_event({"phase": "scheduled", **event})
        return True

    async def _async_free_event_sell_start(self, _now) -> None:
        await self._guarded(self._async_do_free_event_sell_start, "Free event sell start")

    async def _async_do_free_event_sell_start(self) -> None:
        event = dict(self.coordinator.state.free_event)
        if event.get("phase") != "scheduled":
            return
        free_start = dt_util.as_local(dt_util.parse_datetime(event["free_start"]))
        payload = {
            "flux_2": {
                "startTime": dt_util.now().strftime("%H:%M"),
                "endTime": free_start.strftime("%H:%M"),
                "targetSoc": event["sell_floor_soc"],
            }
        }
        api_ok = await self.async_push_flux_override(payload)
        event["phase"] = "selling"
        self.coordinator.update_state(free_event=event)
        title = "🔋 Sunsynk: free event sell started" if api_ok else "⚠️ Sunsynk: free event sell NOT applied"
        await self.async_notify(
            title, f"Selling down to {event['sell_floor_soc']}% ahead of the free period.{_api_note(api_ok)}"
        )

    async def _async_free_event_free_start(self, _now) -> None:
        await self._guarded(self._async_do_free_event_free_start, "Free event free start")

    async def _async_do_free_event_free_start(self) -> None:
        event = dict(self.coordinator.state.free_event)
        if event.get("phase") not in ("scheduled", "selling"):
            return
        free_end = dt_util.as_local(dt_util.parse_datetime(event["free_end"]))
        payload = {
            "flux_1": {
                "startTime": dt_util.now().strftime("%H:%M"),
                "endTime": free_end.strftime("%H:%M"),
                "targetSoc": 100,
            }
        }
        api_ok = await self.async_push_flux_override(payload)
        event["phase"] = "charging"
        self.coordinator.update_state(free_event=event)
        title = "🔋 Sunsynk: free event started" if api_ok else "⚠️ Sunsynk: free event NOT applied"
        await self.async_notify(title, f"Free period active — charging to 100%.{_api_note(api_ok)}")

    async def _async_free_event_free_end(self, _now) -> None:
        await self._guarded(self._async_do_free_event_free_end, "Free event free end")

    async def _async_do_free_event_free_end(self) -> None:
        event = dict(self.coordinator.state.free_event)
        if event.get("phase") not in ("scheduled", "selling", "charging"):
            return
        await self._async_restore_normal_plan()
        event["phase"] = "done"
        self._clear_free_event_timers()
        self.coordinator.update_state(
            free_event=event, free_event_manual_start=None, free_event_manual_end=None
        )
        await self.data_logger.async_log_free_event({"phase": "done", **event})
        await self.async_notify(
            "🔋 Sunsynk: free event finished",
            (
                f"Free period ended. Sell floor {event['sell_floor_soc']}%, "
                f"expected export {event['expected_export_kwh']} kWh, "
                f"expected refill {event['expected_refill_kwh']} kWh. Normal plan restored."
            ),
        )

    async def _async_restore_normal_plan(self) -> None:
        """Re-push the latest computed Flux 1 plan plus the standard Flux 2 slot."""
        plan = self.coordinator.state.last_import_plan
        payload: dict[str, Any] = {"flux_2": {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85}}
        if isinstance(plan, dict) and isinstance(plan.get("payload"), dict) and plan["payload"].get("flux_1"):
            payload["flux_1"] = plan["payload"]["flux_1"]
        await self.async_push_flux_override(payload)

    async def async_cancel_free_event(self) -> None:
        """Cancel a scheduled or in-progress free event (the dashboard 'Cancel free event' button)."""
        event = dict(self.coordinator.state.free_event)
        if event.get("phase") not in ("scheduled", "selling", "charging"):
            return
        was_holding_slots = event.get("phase") in ("selling", "charging")
        self._clear_free_event_timers()
        event["phase"] = "cancelled"
        self.coordinator.update_state(
            free_event=event, free_event_manual_start=None, free_event_manual_end=None
        )
        if was_holding_slots:
            await self._async_restore_normal_plan()
        await self.data_logger.async_log_free_event({"phase": "cancelled", **event})
        await self.async_notify("🔋 Sunsynk: free event cancelled", "The free electricity event was cancelled.")

    # ------------------------------------------------------------------ #
    # Octopus Saving Session (phase 21)                                    #
    # ------------------------------------------------------------------ #

    def _saving_session_pending(self) -> bool:
        """True while a saving session is scheduled, topping up or exporting."""
        return self.coordinator.state.saving_session.get("phase") in _SAVING_SESSION_PENDING

    def _slots_held_by(self) -> str | None:
        """Which event holds the Flux slots right now (do-not-break 7), if any.

        A free event holds them from scheduling; a saving session only while it
        tops up or exports, so the normal plan and evening logic keep running
        in the hours or days before it.
        """
        if self._free_event_active():
            return "free_event"
        if self.coordinator.state.saving_session.get("phase") in ("precharging", "exporting"):
            return "saving_session"
        return None

    def _slots_held(self) -> bool:
        return self._slots_held_by() is not None

    def _saving_session_boost_today(self, now: datetime) -> bool:
        """The 01:55 plan charges to 100% when a scheduled session today is worth filling for."""
        session = self.coordinator.state.saving_session
        if session.get("phase") != "scheduled" or not session.get("offpeak_boost"):
            return False
        start = dt_util.parse_datetime(session.get("session_start") or "")
        return start is not None and dt_util.as_local(start).date() == now.date()

    def _session_export_pence(self, start: datetime, end: datetime, prices: dict[str, float | None]) -> float | None:
        """Export price for the session window: the peak band (Octopus-aware) inside 16:00-19:00, else `charges`."""
        mid = start + (end - start) / 2
        if 16 <= mid.hour < 19:
            return prices.get("export_peak")
        return band_price_pence_per_kwh(self.cfg.get(CONF_CHARGES, []), start.strftime("%H:%M"), end.strftime("%H:%M"), "export")

    def _clear_saving_session_timers(self) -> None:
        for unsub in self._saving_session_unsubs:
            unsub()
        self._saving_session_unsubs = []

    def _arm_saving_session_timers(self, session: dict[str, Any]) -> None:
        """(Re-)arm the top-up, start and end callbacks; past times fire at once (restart resume)."""
        self._clear_saving_session_timers()
        now = dt_util.now()
        phase = session.get("phase")

        def _at(key: str, callback) -> None:
            when = dt_util.as_local(dt_util.parse_datetime(session[key]))
            self._saving_session_unsubs.append(
                async_call_later(self.hass, max(0, (when - now).total_seconds()), callback)
            )

        if phase == "scheduled" and session.get("precharge_start"):
            _at("precharge_start", self._async_saving_session_precharge)
        if phase in ("scheduled", "precharging"):
            _at("session_start", self._async_saving_session_start)
        if phase in _SAVING_SESSION_PENDING:
            _at("session_end", self._async_saving_session_end)

    async def _async_saving_session_push(self, payload: dict[str, Any]) -> bool | None:
        """Flux push for a saving session; None in monitor mode (no API writes, do-not-break 16)."""
        if self.operation_mode == "monitor":
            return None
        return await self.async_push_flux_override(payload)

    async def async_try_schedule_manual_saving_session(self, changed: str = "end") -> None:
        """Called after either manual saving-session datetime entity is set (see the free event twin)."""
        state = self.coordinator.state
        if not state.saving_session_manual_start or not state.saving_session_manual_end:
            return
        start = dt_util.as_local(dt_util.parse_datetime(state.saving_session_manual_start))
        end = dt_util.as_local(dt_util.parse_datetime(state.saving_session_manual_end))
        now = dt_util.now()
        error = manual_free_event_error(changed, start, end, now, "Saving session")
        if error:
            await self.async_notify("⚠️ Sunsynk: saving session NOT scheduled", error)
            return
        if end <= start or start <= now:
            return  # stale counterpart: wait for the other field to be set
        await self.async_schedule_saving_session(start, end, source="manual")

    async def _async_saving_session_entity_changed(self, _event: Event) -> None:
        await self._guarded(self.async_check_octopus_saving_session, "Saving session auto-detect")

    async def async_check_octopus_saving_session(self) -> None:
        """Schedule the next joined session from the Octopus events entity (blank config = off).

        Runs on entity changes, every 30 minutes and after a session ends. A
        session already seen (scheduled, done or cancelled) is not re-added;
        one that clashes with a pending event waits until that event ends.
        """
        entity = self._cfg_str(CONF_OCTOPUS_SAVING_SESSION_ENTITY)
        state = self.hass.states.get(entity) if entity else None
        if state is None:
            return
        found = next_joined_saving_session(dict(state.attributes), dt_util.now())
        if found is None:
            return
        start, end, reward = (dt_util.as_local(found[0]), dt_util.as_local(found[1]), found[2])
        if self.coordinator.state.saving_session.get("session_start") == start.isoformat():
            return
        if self._saving_session_pending() or self._free_event_active():
            return
        await self.async_schedule_saving_session(start, end, source="octopus", reward_pence=reward)

    async def async_schedule_saving_session(
        self, start: datetime, end: datetime, source: str, reward_pence: float | None = None
    ) -> bool:
        """Plan and arm a saving session. Returns True if it was scheduled."""
        if self._saving_session_pending() or self._free_event_active():
            await self.async_notify(
                "⚠️ Sunsynk: saving session NOT scheduled",
                "A saving session or free event is already scheduled or in progress. Cancel it first.",
            )
            return False
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            await self.async_notify(
                "⚠️ Sunsynk: saving session NOT scheduled",
                f"Battery SOC entity {self.battery_soc_entity} unavailable.",
            )
            return False

        cfg = self.cfg
        capacity_kwh = max(0.1, float(cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        default_rate = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        charge_rate_kw = float(cfg.get(CONF_FREE_EVENT_CHARGE_RATE_KW) or default_rate)
        export_rate_kw = float(cfg.get(CONF_FREE_EVENT_EXPORT_RATE_KW) or default_rate)
        if reward_pence is None and cfg.get(CONF_SAVING_SESSION_REWARD_PENCE):
            reward_pence = float(cfg[CONF_SAVING_SESSION_REWARD_PENCE])
        prices, sources = self._tariff_prices(start.date())
        export_pence = self._session_export_pence(start, end, prices)
        plan = plan_saving_session(
            start, end, soc, capacity_kwh, charge_rate_kw, export_rate_kw, self._evening_reserve_soc(),
            reward_pence, export_pence, prices["offpeak"], prices["day"],
        )
        session = {
            "source": source,
            "session_start": start.isoformat(),
            "session_end": end.isoformat(),
            "reward_pence": reward_pence,
            "export_pence": export_pence,
            "offpeak_pence": prices["offpeak"],
            "day_pence": prices["day"],
            "price_source": price_source(sources, "offpeak", "day"),
            "value_pence": plan.value_pence,
            "floor_soc": plan.floor_soc,
            "floor_reason": plan.floor_reason,
            "offpeak_boost": plan.offpeak_boost,
            "precharge": plan.precharge,
            "precharge_start": plan.precharge_start.isoformat() if plan.precharge_start else None,
            "precharge_end": plan.precharge_end.isoformat() if plan.precharge_end else None,
            "expected_export_kwh": plan.expected_export_kwh,
            "soc_at_schedule": soc,
            "charge_rate_kw": charge_rate_kw,
            "export_rate_kw": export_rate_kw,
            "phase": "scheduled",
        }
        self.coordinator.update_state(saving_session=session)
        self._arm_saving_session_timers(session)

        floor_note = (
            "the reward beats buying it back" if plan.floor_reason == "reward_beats_rebuy" else "keeping the evening reserve"
        )
        fill_notes = []
        if plan.offpeak_boost and start.date() > dt_util.now().date():
            fill_notes.append("The 01:55 plan on the day charges to 100%.")
        if plan.precharge:
            fill_notes.append(
                f"Day-rate top-up {plan.precharge_start.strftime('%H:%M')} → {plan.precharge_end.strftime('%H:%M')}."
            )
        monitor_note = " Monitor mode: the plan is tracked but the inverter won't be changed." if self.operation_mode == "monitor" else ""
        await self.async_notify(
            "🔋 Sunsynk: saving session scheduled",
            (
                f"Session {start.strftime('%a %d %b %H:%M')} → {end.strftime('%H:%M')} ({source}). "
                f"Worth {_pence(plan.value_pence)}/kWh (reward {_pence(reward_pence)} + export {_pence(export_pence)}). "
                f"Exporting down to {plan.floor_soc}% ({floor_note}), expected {plan.expected_export_kwh} kWh. "
                f"{' '.join(fill_notes)}{monitor_note}"
            ).strip(),
        )
        await self.data_logger.async_log_saving_session({"phase": "scheduled", **session})
        return True

    async def _async_saving_session_precharge(self, _now) -> None:
        await self._guarded(self._async_do_saving_session_precharge, "Saving session top-up")

    async def _async_do_saving_session_precharge(self) -> None:
        session = dict(self.coordinator.state.saving_session)
        if session.get("phase") != "scheduled" or not session.get("precharge_end"):
            return
        now = dt_util.now()
        precharge_end = dt_util.as_local(dt_util.parse_datetime(session["precharge_end"]))
        if precharge_end <= now:
            return  # scheduled too late to top up; the session itself still runs
        api_ok = await self._async_saving_session_push(
            {"flux_1": {"startTime": now.strftime("%H:%M"), "endTime": precharge_end.strftime("%H:%M"), "targetSoc": 100}}
        )
        session["phase"] = "precharging"
        self.coordinator.update_state(saving_session=session)
        title = (
            "⚠️ Sunsynk: saving session top-up NOT applied" if api_ok is False
            else "🔋 Sunsynk: saving session top-up started"
        )
        await self.async_notify(
            title, f"Charging to 100% until {precharge_end.strftime('%H:%M')} ahead of the session.{_session_api_note(api_ok)}"
        )

    async def _async_saving_session_start(self, _now) -> None:
        await self._guarded(self._async_do_saving_session_start, "Saving session start")

    async def _async_do_saving_session_start(self) -> None:
        session = dict(self.coordinator.state.saving_session)
        if session.get("phase") not in ("scheduled", "precharging"):
            return
        now = dt_util.now()
        session_end = dt_util.as_local(dt_util.parse_datetime(session["session_end"]))
        api_ok = await self._async_saving_session_push(
            {"flux_2": {"startTime": now.strftime("%H:%M"), "endTime": session_end.strftime("%H:%M"), "targetSoc": session["floor_soc"]}}
        )
        session["phase"] = "exporting"
        session["soc_at_start"] = self._essential_state(self.battery_soc_entity)
        self.coordinator.update_state(saving_session=session)
        title = "⚠️ Sunsynk: saving session NOT applied" if api_ok is False else "🔋 Sunsynk: saving session started"
        await self.async_notify(
            title,
            f"Running the house from the battery and exporting down to {session['floor_soc']}% "
            f"until {session_end.strftime('%H:%M')}.{_session_api_note(api_ok)}",
        )

    async def _async_saving_session_end(self, _now) -> None:
        await self._guarded(self._async_do_saving_session_end, "Saving session end")

    async def _async_do_saving_session_end(self) -> None:
        session = dict(self.coordinator.state.saving_session)
        if session.get("phase") not in _SAVING_SESSION_PENDING:
            return
        held_slots = session.get("phase") in ("precharging", "exporting")
        if held_slots and self.operation_mode != "monitor":
            await self._async_restore_normal_plan()
        session["phase"] = "done"
        session["soc_at_end"] = self._essential_state(self.battery_soc_entity)
        self._clear_saving_session_timers()
        self.coordinator.update_state(
            saving_session=session, saving_session_manual_start=None, saving_session_manual_end=None
        )
        await self.data_logger.async_log_saving_session({"phase": "done", **session})
        await self.async_notify(
            "🔋 Sunsynk: saving session finished",
            (
                f"Session ended. SOC {_pct(session.get('soc_at_start'))} → {_pct(session.get('soc_at_end'))} "
                f"(floor {session['floor_soc']}%, expected export {session['expected_export_kwh']} kWh). "
                "Normal plan restored."
            ),
        )
        await self.async_check_octopus_saving_session()

    async def async_cancel_saving_session(self) -> None:
        """Cancel a scheduled or in-progress saving session (the dashboard button)."""
        session = dict(self.coordinator.state.saving_session)
        if session.get("phase") not in _SAVING_SESSION_PENDING:
            return
        held_slots = session.get("phase") in ("precharging", "exporting")
        self._clear_saving_session_timers()
        session["phase"] = "cancelled"
        self.coordinator.update_state(
            saving_session=session, saving_session_manual_start=None, saving_session_manual_end=None
        )
        if held_slots and self.operation_mode != "monitor":
            await self._async_restore_normal_plan()
        await self.data_logger.async_log_saving_session({"phase": "cancelled", **session})
        await self.async_notify("🔋 Sunsynk: saving session cancelled", "The saving session was cancelled.")

    # ------------------------------------------------------------------ #
    # Gentler charging (phase 23)                                          #
    # ------------------------------------------------------------------ #

    def _gentle_charge_due(self, cfg: dict[str, Any], now: datetime, current_a: int | None, max_a: float) -> bool:
        """Live gentle charge applies before 05:00 when it actually lowers the current."""
        return (
            bool(cfg.get(CONF_GENTLE_CHARGE_LIVE, DEFAULT_GENTLE_CHARGE_LIVE))
            and current_a is not None
            and current_a < max_a
            and not self._slots_held()
            and now.hour * 60 + now.minute < hhmm_to_minutes(CHEAP_WINDOW_END)
        )

    async def _async_write_setting(self, key: str, value: Any) -> tuple[bool, float | None]:
        """Read the inverter settings, change one key, write them all back.

        The settings-endpoint twin of _async_post_with_status (do-not-break 5):
        surfaces failures via last_api_result / last_error and returns
        (ok, previous value). Refuses to write when the current value can't be
        read as a number, so there is always something to restore.
        """
        serial = self.inverter_serial
        try:
            settings = await self.coordinator.api.async_read_settings(serial)
            previous = float(settings[key])
            await self.coordinator.api.async_write_settings(serial, {**settings, key: str(value)})
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Sunsynk settings write failed (%s)", key)
            self.coordinator.update_state(
                last_api_result={"ok": False, "error": str(exc)},
                last_error=f"Settings write failed ({key}): {exc}",
            )
            return False, None
        self.coordinator.update_state(last_api_result={"ok": True, "setting": key, "value": value})
        return True, previous

    async def _async_apply_gentle_charge(self, current_a: int, now: datetime) -> bool:
        """Write the gentle grid-charge current and arm its 05:00 restore."""
        pending = self.coordinator.state.gentle_charge
        ok, previous = await self._async_write_setting(GENTLE_CHARGE_SETTING, current_a)
        if not ok:
            return False
        # A re-plan before 05:00 (reload) reads back our own gentle value; keep
        # the original one to restore.
        restore = pending["restore_current_a"] if pending.get("phase") == "applied" else previous
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
        return True

    def _arm_gentle_restore(self) -> None:
        if self._gentle_restore_unsub:
            self._gentle_restore_unsub()
        restore_at = dt_util.as_local(dt_util.parse_datetime(self.coordinator.state.gentle_charge["restore_at"]))
        delay = max(0, (restore_at - dt_util.now()).total_seconds())
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
