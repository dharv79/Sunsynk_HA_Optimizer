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
from datetime import timedelta
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
    CONF_FLUX_PRODUCTS,
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
    CONF_WEATHER_ENTITY,
    DEFAULT_AVG_CONSUMPTION_KW,
    DEFAULT_WEEKEND_AVG_CONSUMPTION_KW,
    DEFAULT_AWAY_AVG_CONSUMPTION_KW,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGE_RATE,
    DEFAULT_ENABLE_AI_WEEKLY_INSIGHT,
    DEFAULT_EXPORT_DISABLE_COST_THRESHOLD_PENCE_PER_HOUR,
    DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE,
    DEFAULT_HOURLY_FORECAST_ATTRIBUTE,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SOLAR_START_OFFSET_HOURS,
    FULL_CHARGE_DAY_OPTIONS,
)
from .data_logger import DAILY_COST_FIELDS, DataLogger
from .flux_helpers import apply_flux_override, build_payload, merge_entry_data, peak_import_price_pence_per_kwh
from .planning import (
    LOW_SOLAR_THRESHOLD_KWH,
    WEEK_HISTORY_DAYS,
    apply_soc_adjustments,
    days_in_period,
    flux1_end_minutes,
    forecast_band,
    minutes_to_hhmm,
    net_cost_gbp,
    resolve_used_charge_rate,
    score_full_charge_day,
    select_target_soc,
    sum_field,
    synthetic_hourly_profile,
    trailing_week,
)

_LOGGER = logging.getLogger(__name__)

# How many days back an Octopus "previous accumulative cost" reading is still
# accepted when its settlement is running behind schedule (see
# SunsynkOptimizer._read_octopus_previous_day_cost).
_OCTOPUS_CATCH_UP_DAYS = 5

_UNAVAILABLE_STATES = ("unknown", "unavailable", "none", "")


def _api_note(api_ok: bool) -> str:
    return "" if api_ok else " (inverter NOT updated)"


class SunsynkOptimizer:
    """Implements optimizer behaviour."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.unsubs: list[Any] = []
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
            (6, 0, self._async_capture_morning_state),
            (22, 0, self._async_capture_day_actuals),
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

        if self.coordinator.state.selected_full_charge_day is None:
            self.coordinator.update_state(
                selected_full_charge_day=self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY]
            )

        self.coordinator.update_state(operation_mode=self.operation_mode)
        self.unsubs.append(async_call_later(self.hass, 60, self._async_initial_refresh))

    async def async_shutdown(self) -> None:
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
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
            return

        forecast_items = []
        if isinstance(response, dict):
            weather_data = response.get(weather_entity)
            if isinstance(weather_data, dict):
                forecast_items = weather_data.get("forecast", []) or []

        scores: dict[str, float] = {day: -999.0 for day in FULL_CHARGE_DAY_OPTIONS}
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
        forecast_correction = self.data_logger.compute_forecast_correction(paired_days)
        # When the forecast sensor was unavailable we already used yesterday's raw
        # value; don't apply the correction factor a second time on top of values
        # that may already have been corrected by the prior plan run.
        applied_correction = 1.0 if forecast_fallback else forecast_correction
        solar_forecast_kwh = round(raw_forecast_kwh * applied_correction, 2)
        band = forecast_band(solar_forecast_kwh)
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
        if target_soc < 100:
            # Drain and evening-nudge corrections apply whenever the target has
            # headroom — regular nights and full-charge-day solar-bridge plans.
            overnight_drain_adjustment = self.data_logger.compute_overnight_drain_adjustment(paired_days, away)
            soc_adjustment = self.data_logger.compute_soc_target_adjustment(paired_days, band, away)
            target_soc = apply_soc_adjustments(target_soc, overnight_drain_adjustment, soc_adjustment)

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

        if low_solar:
            # Solar is scarce — take all the cheap import we can get.
            flux1_end = "05:00"
            logic_branch = "low_solar_full_window"
        else:
            # Physics-based window: charge exactly as long as needed to reach target.
            energy_needed_kwh = max(0.0, (target_soc - soc) / 100.0 * battery_capacity_kwh)
            flux1_end = minutes_to_hhmm(flux1_end_minutes(energy_needed_kwh, used_charge_rate))
            logic_branch = "adaptive_hourly" if decision.hourly_forecast_used else "adaptive"
        next_import_window = f"02:00→{flux1_end}"

        payload = {
            "flux_1": {"startTime": "02:00", "endTime": flux1_end, "targetSoc": target_soc},
            "flux_2": {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85},
        }
        api_ok = None if dry_run else await self.async_push_flux_override(payload)

        plan_state = {
            "date": now.date().isoformat(),
            "today": today,
            "selected_full_charge_day": full_day,
            "is_full_day": is_full_day,
            "soc": soc,
            "raw_forecast_kwh": raw_forecast_kwh,
            "forecast_correction_factor": forecast_correction,
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
            "forecast_correction_days": self.data_logger.count_forecast_correction_days(paired_days),
            "effective_charge_rate_kw": effective_charge_rate,
            "charge_rate_from_cache": computed_charge_rate is None,
            "used_charge_rate_kw": used_charge_rate,
            "charge_rate_calibration_days": self.data_logger.count_charge_rate_calibration_days(paired_days),
            "flux1_end": flux1_end,
            "next_import_window": next_import_window,
            "payload": payload,
            "source": source,
            "api_ok": api_ok,
            "forecast_fallback": forecast_fallback,
            "is_weekend": is_weekend,
            "away": away,
            "avg_consumption_kw": avg_consumption_kw,
            "battery_temp_c": battery_temp_c,
            "temp_deration_factor": temp_factor,
            "hourly_forecast_used": decision.hourly_forecast_used,
            "synthetic_ramp": decision.synthetic_ramp,
            "bridge_hour": decision.bridge_hour,
        }

        if dry_run:
            await self.async_notify(
                f"🧪 Sunsynk: test plan (dry run) — {plan_state['date']}",
                json.dumps(plan_state),
            )
            return

        await self.data_logger.async_log_import_plan(plan_state)

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
        api_ok = plan["api_ok"]
        title = "🔋 Sunsynk: import plan set" if api_ok else "⚠️ Sunsynk: import plan NOT applied"
        api_note = "" if api_ok else " Inverter NOT updated; will retry next cycle."
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
            peak_price = peak_import_price_pence_per_kwh(cfg.get(CONF_CHARGES, []))
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
        if not is_full_day and soc > 85 and self._cooldown_ok():
            self._mark_trim()
            api_ok = await self._async_push_flux2_action(
                "trim_to_82",
                {
                    "startTime": now_local.strftime("%H:%M"),
                    "endTime": (now_local + timedelta(minutes=45)).strftime("%H:%M"),
                    "targetSoc": 82,
                },
                base_action,
                f"soc_{round(soc)}%_exceeds_85",
            )
            title = "🔋 Sunsynk: battery trim" if api_ok else "⚠️ Sunsynk: battery trim NOT applied"
            await self.async_notify(
                title,
                f"SOC {round(soc, 1)}% is above 85%. Trimming to 82%.{_api_note(api_ok)}",
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
        if self.coordinator.state.evening_export_disabled and 16 <= now.hour < 19:
            _LOGGER.info("Skipping initial import plan — evening export pause active")
            return
        await self._guarded(self.async_run_import_plan, "Initial refresh")

    async def _async_choose_best_full_charge_day(self, _now) -> None:
        """Time-change callback at 18:00 daily — only acts on Sundays."""
        if dt_util.now().strftime("%A") == "Sunday":
            await self._guarded(self.async_choose_best_full_charge_day, "Full-charge-day selection")
            await self._guarded(self._async_send_weekly_cost_summary, "Weekly cost summary")
            await self._guarded(self._async_send_ai_weekly_insight, "AI weekly insight")

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

    async def _async_periodic_flux2_check(self, _now) -> None:
        """30-minute interval callback."""
        await self._guarded(self.async_run_flux2_check, "Periodic Flux 2 check")
        await self._guarded(self._async_track_peak_window_usage, "Peak-window usage tracking")

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
                now_local = dt_util.now()
                api_ok = await self._async_push_flux2_action(
                    "full_day_trim_to_82",
                    {
                        "startTime": now_local.strftime("%H:%M"),
                        "endTime": (now_local + timedelta(minutes=60)).strftime("%H:%M"),
                        "targetSoc": 82,
                    },
                    {
                        "soc": current_soc,
                        "grid_pac": self._state_float(self.grid_pac_entity, 0),
                        "source": "automatic",
                    },
                    "held_100%_for_1h",
                )
                title = "🔋 Sunsynk: full-charge hold complete" if api_ok else "⚠️ Sunsynk: full-charge trim NOT applied"
                await self.async_notify(
                    title,
                    f"Held at 100% for 1 hour. Trimming to 82%.{_api_note(api_ok)}",
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
        overnight_load_kwh = self._state_float(self.day_load_entity, 0)
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
                "overnight_load_kwh": round(overnight_load_kwh, 2),
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
        meters = self._read_daily_meters()
        day_load_kwh = meters["load"]
        day_grid_import_kwh = meters["grid_import"]
        day_grid_export_kwh = meters["grid_export"]
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
            "day_load_kwh": round(day_load_kwh, 2),
            "day_grid_import_kwh": round(day_grid_import_kwh, 2),
            "day_grid_export_kwh": round(day_grid_export_kwh, 2),
        }
        # Persisted (not just logged/notified) so the dashboard's Consumption
        # sensor has today's actuals to display, the same way last_morning_state
        # and last_peak_window_usage are kept for their own dashboard fields.
        self.coordinator.update_state(touch=False, last_day_actuals=actuals_rec)
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            plan_rec = self.coordinator.state.last_import_plan or {}
            morning_rec = self.coordinator.state.last_morning_state or {}
            # Only include today's peak-window record — a stale prior-day value
            # (e.g. the window was never captured today because of a restart)
            # shouldn't be re-sent under today's date.
            peak_rec = self.coordinator.state.last_peak_window_usage or {}
            if peak_rec.get("date") != date:
                peak_rec = {}
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
            lines = "\n".join(
                json.dumps(r)
                for r in [plan_rec, morning_rec, actuals_rec, peak_rec, cost_rec]
                if r
            )
            await self.async_notify(
                f"Sunsynk Daily Data — {date}",
                lines,
                target=data_report_target,
            )
