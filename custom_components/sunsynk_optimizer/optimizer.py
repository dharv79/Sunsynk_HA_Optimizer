# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Core optimizer logic."""

from __future__ import annotations

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
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_RATE,
    CONF_DATA_REPORT_TARGET,
    CONF_DEFAULT_FULL_CHARGE_DAY,
    CONF_EXPORT_DISABLE_THRESHOLD,
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
    CONF_WEATHER_ENTITY,
    DEFAULT_AVG_CONSUMPTION_KW,
    DEFAULT_WEEKEND_AVG_CONSUMPTION_KW,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGE_RATE,
    DEFAULT_HOURLY_FORECAST_ATTRIBUTE,
    DEFAULT_OPERATION_MODE,
    DEFAULT_SOLAR_START_OFFSET_HOURS,
    FULL_CHARGE_DAY_OPTIONS,
)
from .data_logger import DataLogger
from .flux_helpers import apply_flux_override, build_payload, merge_entry_data

_LOGGER = logging.getLogger(__name__)


class SunsynkOptimizer:
    """Implements optimizer behaviour."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, coordinator) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.unsubs: list[Any] = []
        self.last_trim_ts: float | None = None
        self.pending_full_trim_cancel = None
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

    @property
    def battery_soc_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_battery_soc"

    @property
    def grid_pac_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_grid_pac"

    @property
    def day_pv_energy_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_pv_etoday"

    @property
    def pv_mppt0_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_pv_mppt0_power"

    @property
    def pv_mppt1_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_pv_mppt1_power"

    @property
    def battery_temp_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_battery_temperature"

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
        self.unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_choose_best_full_charge_day,
                hour=18,
                minute=0,
                second=0,
            )
        )
        self.unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_run_import_plan,
                hour=1,
                minute=55,
                second=0,
            )
        )
        self.unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic_flux2_check,
                timedelta(minutes=30),
            )
        )
        self.unsubs.append(
            async_track_state_change_event(
                self.hass,
                [self.battery_soc_entity],
                self._async_battery_soc_changed,
            )
        )
        self.unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_capture_morning_state,
                hour=6,
                minute=0,
                second=0,
            )
        )
        self.unsubs.append(
            async_track_time_change(
                self.hass,
                self._async_capture_day_actuals,
                hour=22,
                minute=0,
                second=0,
            )
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

    def _state_float(self, entity_id: str, default: float = 0.0) -> float:
        """Return the numeric state of an entity, or `default` if unavailable/unparseable."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _essential_state(self, entity_id: str) -> float | None:
        """Like _state_float but returns None when the entity is missing/unavailable.

        Use for inputs whose absence should abort the cycle (battery_soc, forecast)
        rather than silently defaulting to 0 and producing a worst-case action.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _cooldown_ok(self, seconds: int = 1800) -> bool:
        """Return True if at least `seconds` have elapsed since the last trim action."""
        if self.last_trim_ts is None:
            return True
        return (dt_util.utcnow().timestamp() - self.last_trim_ts) > seconds

    def _mark_trim(self) -> None:
        """Record the current time as the last trim timestamp for cooldown tracking."""
        self.last_trim_ts = dt_util.utcnow().timestamp()

    def _forecast_band(self, forecast_kwh: float) -> str:
        """Classify adjusted solar forecast into a seasonal band used for SOC target selection."""
        if forecast_kwh >= 10:
            return "summer_like"
        if forecast_kwh <= 5:
            return "winter_like"
        return "shoulder"

    async def async_notify(self, title: str, message: str, target: str | None = None) -> None:
        """Send a notification via the configured HA notify service."""
        service_string = str(self.cfg.get(CONF_NOTIFY_SERVICE, "")).strip()
        if "." not in service_string:
            self.coordinator.update_state(
                last_error=f"Invalid notify service: {service_string}",
                last_notification={
                    "ok": False,
                    "service": service_string,
                    "title": title,
                    "message": message,
                },
            )
            return

        domain, service = service_string.split(".", 1)
        data: dict[str, Any] = {"title": title, "message": message}
        notify_target = target or str(self.cfg.get(CONF_NOTIFY_TARGET, "")).strip()
        if notify_target:
            data["target"] = [notify_target]

        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
            self.coordinator.update_state(
                last_notification={
                    "ok": True,
                    "service": service_string,
                    "target": notify_target or None,
                    "title": title,
                    "message": message,
                }
            )
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Notification failed")
            self.coordinator.update_state(
                last_error=f"Notification failed: {exc}",
                last_notification={
                    "ok": False,
                    "service": service_string,
                    "target": notify_target or None,
                    "title": title,
                    "message": message,
                    "error": str(exc),
                },
            )

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
        config = self.cfg
        payload = build_payload(config)
        return await self._async_post_with_status(payload)

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
        full_payload = build_payload(config, flux_products)
        return await self._async_post_with_status(full_payload)

    async def async_reset_flux_baseline(self) -> None:
        config = self.cfg
        baseline = config.get(CONF_FLUX_PRODUCTS, [])
        payload = build_payload(config, baseline)
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
        status_prefix = "" if ok else "⚠ API push FAILED — "
        await self.async_notify(
            f"{status_prefix}🔋 Sunsynk baseline restored",
            "Flux baseline settings were restored.",
        )

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
            if day_name not in FULL_CHARGE_DAY_OPTIONS:
                continue

            condition = str(item.get("condition", "unknown"))
            cloud = float(item.get("cloud_coverage", 50) or 50)
            rain = float(item.get("precipitation_probability", 0) or 0)
            temp = float(item.get("temperature", 15) or 15)

            # Base: start at 100 and subtract cloud and rain impact.
            # Rain weighted at 0.7× because partial rain still allows some generation.
            score = 100 - cloud - (rain * 0.7)

            # Condition string adjustments — weather entity values from HA weather domain.
            if condition in ["sunny", "clear"]:
                score += 25
            elif condition in ["partlycloudy"]:
                score += 10
            elif condition in ["cloudy", "fog"]:
                score -= 10
            elif condition in ["rainy", "pouring", "lightning-rainy", "snowy", "snowy-rainy"]:
                score -= 25

            # Temperature nudge: warmer days tend to have longer usable solar hours.
            if temp >= 18:
                score += 3
            elif temp <= 5:
                score -= 3

            # Later-in-week penalty: if we fill up on Thursday or Friday there's less
            # week remaining to use the stored energy before the next weekend.
            if day_name == "Thursday":
                score -= 5
            elif day_name == "Friday":
                score -= 15

            scores[day_name] = round(score, 1)

        best_day = sorted(scores.items(), key=lambda x: x[1], reverse=True)[0][0]
        self.coordinator.update_state(
            selected_full_charge_day=best_day,
            last_full_charge_scores=scores,
        )

        await self.data_logger.async_log_full_charge_scores(scores, best_day)

        await self.async_notify(
            "🔋 Sunsynk Full Charge Day Updated",
            (
                f"Chosen day: {best_day}. "
                f"Scores - Monday: {scores['Monday']}, "
                f"Tuesday: {scores['Tuesday']}, "
                f"Wednesday: {scores['Wednesday']}, "
                f"Thursday: {scores['Thursday']}, "
                f"Friday: {scores['Friday']}."
            ),
        )

    def _get_hourly_forecast_kwh(self) -> dict[int, float] | None:
        """Return hourly solar forecast as {hour: kWh}, or None if unavailable/unconfigured.

        Supports two attribute formats:
        - Forecast.Solar: dict keyed by "YYYY-MM-DD HH:MM:SS" with float kWh per hour
        - Solcast: list of {"period_start": iso_str, "pv_estimate": kWh per 30-min period}
          (consecutive 30-min periods are summed to hourly buckets)
        """
        sensor_id = str(self.cfg.get(CONF_HOURLY_FORECAST_SENSOR, "")).strip()
        if not sensor_id:
            return None
        state = self.hass.states.get(sensor_id)
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            return None
        attr_name = str(self.cfg.get(CONF_HOURLY_FORECAST_ATTRIBUTE, DEFAULT_HOURLY_FORECAST_ATTRIBUTE)).strip()
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
        soc_val = self._essential_state(self.battery_soc_entity)
        if soc_val is None:
            msg = f"Battery SOC entity {self.battery_soc_entity} unavailable — import plan skipped"
            _LOGGER.warning(msg)
            self.coordinator.update_state(last_error=msg)
            await self.async_notify("⚠ Sunsynk plan skipped", msg)
            return
        soc = soc_val

        today = dt_util.now().strftime("%A")
        full_day = self.selected_full_charge_day
        is_full_day = today == full_day
        forecast_entity = self.cfg[CONF_SOLAR_FORECAST_SENSOR]

        # Pre-flight: forecast is essential because solar_forecast_kwh < 7 toggles
        # the maximum-import low-solar override. A missing forecast read as 0
        # would push 100% target + full window unnecessarily.
        forecast_val = self._essential_state(forecast_entity)
        forecast_fallback_note = ""
        if forecast_val is None:
            last_plan = self.coordinator.state.last_import_plan or {}
            prior_raw = last_plan.get("raw_forecast_kwh") if isinstance(last_plan, dict) else None
            prior_date = last_plan.get("date") if isinstance(last_plan, dict) else None
            today_str = dt_util.now().strftime("%Y-%m-%d")
            prior_too_old = prior_date != today_str and prior_date != (
                dt_util.now() - timedelta(days=1)
            ).strftime("%Y-%m-%d")
            if prior_raw is None or not isinstance(prior_raw, (int, float)) or prior_too_old:
                msg = (
                    f"Forecast sensor {forecast_entity} unavailable and no usable prior plan — "
                    "import plan skipped"
                )
                _LOGGER.warning(msg)
                self.coordinator.update_state(last_error=msg)
                await self.async_notify("⚠ Sunsynk plan skipped", msg)
                return
            raw_forecast_kwh = float(prior_raw)
            forecast_fallback_note = " (using yesterday's forecast — sensor unavailable)"
        else:
            raw_forecast_kwh = forecast_val

        battery_capacity_kwh = max(0.1, float(self.cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))
        charge_rate_kw = float(self.cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        avg_consumption_kw = float(self.cfg.get(CONF_AVG_CONSUMPTION_KW, DEFAULT_AVG_CONSUMPTION_KW))
        weekend_avg_consumption_kw = float(self.cfg.get(CONF_WEEKEND_AVG_CONSUMPTION_KW, DEFAULT_WEEKEND_AVG_CONSUMPTION_KW))
        is_weekend = today in ("Saturday", "Sunday")
        if is_weekend:
            avg_consumption_kw = weekend_avg_consumption_kw
        solar_start_offset_hours = float(self.cfg.get(CONF_SOLAR_START_OFFSET_HOURS, DEFAULT_SOLAR_START_OFFSET_HOURS))

        paired_days = await self.data_logger.async_load_paired_days(days=30)
        forecast_correction = self.data_logger.compute_forecast_correction(paired_days)
        # When the forecast sensor was unavailable we already used yesterday's raw
        # value; don't apply the correction factor a second time on top of values
        # that may already have been corrected by the prior plan run.
        if forecast_fallback_note:
            solar_forecast_kwh = round(raw_forecast_kwh, 2)
        else:
            solar_forecast_kwh = round(raw_forecast_kwh * forecast_correction, 2)
        forecast_band = self._forecast_band(solar_forecast_kwh)
        hourly_forecast_kwh = self._get_hourly_forecast_kwh()
        hourly_forecast_used = False
        bridge_hour: int | None = None

        # Compute solar start time from sun.sun entity (sunrise + configured offset).
        solar_start_time: str | None = None
        hours_to_solar: float = 0.0
        sun_state = self.hass.states.get("sun.sun")
        if sun_state:
            next_rising_str = sun_state.attributes.get("next_rising")
            if next_rising_str:
                next_rising = dt_util.parse_datetime(next_rising_str)
                if next_rising:
                    solar_start_dt = dt_util.as_local(next_rising) + timedelta(hours=solar_start_offset_hours)
                    solar_start_time = solar_start_dt.strftime("%H:%M")
                    solar_start_hours = solar_start_dt.hour + solar_start_dt.minute / 60.0
                    # Use 05:00 as the worst-case charge window end to avoid circularity.
                    hours_to_solar = max(0.0, solar_start_hours - 5.0)

        if is_full_day:
            soc_reason = "weekly_full_charge_day"
            if solar_forecast_kwh >= 7 and solar_start_time is not None:
                # Good solar expected — bridge overnight import to solar start time;
                # PV charges the battery to 100% during the day for free.
                energy_to_cover = hours_to_solar * avg_consumption_kw
                bridge_soc = int(20 + energy_to_cover / battery_capacity_kwh * 100)
                target_soc = max(30, min(100, bridge_soc))
            else:
                # Poor solar forecast on the chosen day — import fully from grid as fallback.
                target_soc = 100
        else:
            if solar_forecast_kwh < 7:
                # Very low solar — fill up regardless of band to ensure enough energy.
                if forecast_band == "winter_like":
                    target_soc = 100
                    soc_reason = "low_solar_override_winter_like"
                else:
                    target_soc = 95
                    soc_reason = "low_solar_override"
            elif hourly_forecast_kwh:
                # Hourly forecast available (Forecast.Solar / Solcast): walk hours 5–12 and
                # accumulate the energy deficit until solar generation covers home load.
                hourly_correction = forecast_correction if not forecast_fallback_note else 1.0
                energy_gap = 0.0
                for h in range(5, 13):
                    solar_h = hourly_forecast_kwh.get(h, 0.0) * hourly_correction
                    if solar_h >= avg_consumption_kw:
                        if bridge_hour is None:
                            bridge_hour = h
                        break
                    energy_gap += max(0.0, avg_consumption_kw - solar_h)
                bridge_soc = int(20 + energy_gap / battery_capacity_kwh * 100)
                target_soc = max(30, min(100, bridge_soc))
                soc_reason = "solar_bridge_hourly"
                hourly_forecast_used = True
            elif solar_start_time is not None:
                # Charge only enough to bridge from charge window end (05:00) to when solar covers load.
                energy_to_cover = hours_to_solar * avg_consumption_kw
                target_soc = int(20 + energy_to_cover / battery_capacity_kwh * 100)
                target_soc = max(30, min(100, target_soc))
                soc_reason = "solar_bridge"
            else:
                # sun.sun unavailable — fall back to band-based targets.
                if forecast_band == "winter_like":
                    target_soc = 95
                    soc_reason = "winter_like"
                elif forecast_band == "summer_like":
                    target_soc = 80
                    soc_reason = "summer_like"
                else:
                    target_soc = 85
                    soc_reason = "shoulder"

        overnight_drain_adjustment = 0
        soc_adjustment = 0
        if target_soc < 100:
            # Apply drain and evening-nudge corrections whenever the import target has
            # headroom below 100%. This covers both regular nights and full-charge-day
            # solar-bridge plans. Skipped when target is already 100% (nothing to add).
            overnight_drain_adjustment = self.data_logger.compute_overnight_drain_adjustment(
                paired_days
            )
            soc_adjustment = self.data_logger.compute_soc_target_adjustment(
                paired_days, forecast_band
            )
            target_soc = max(20, min(100, target_soc + overnight_drain_adjustment + soc_adjustment))
            # Ensure estimated morning SOC (at 06:00) stays above safe minimum.
            # overnight_drain_adjustment reflects the measured drop from charge end to 06:00.
            if overnight_drain_adjustment > 0:
                estimated_morning_soc = target_soc - overnight_drain_adjustment
                if estimated_morning_soc < 25:
                    target_soc = min(100, 25 + overnight_drain_adjustment)

        forecast_correction_days = self.data_logger.count_forecast_correction_days(paired_days)
        overnight_drain_days = self.data_logger.count_drain_adjustment_days(paired_days)
        soc_adjustment_days = self.data_logger.count_soc_adjustment_days(paired_days, forecast_band)

        effective_charge_rate = self.data_logger.compute_effective_charge_rate_kw(
            paired_days, battery_capacity_kwh, overnight_drain_adjustment
        )
        charge_rate_calibration_days = self.data_logger.count_charge_rate_calibration_days(paired_days)
        used_charge_rate = (
            min(charge_rate_kw, effective_charge_rate)
            if effective_charge_rate is not None and effective_charge_rate < charge_rate_kw * 0.9
            else charge_rate_kw
        )

        battery_temp_c = self._state_float(self.battery_temp_entity, 20.0)
        if battery_temp_c > 15:
            temp_deration_factor = 1.0
        elif battery_temp_c > 10:
            temp_deration_factor = 0.85
        elif battery_temp_c > 5:
            temp_deration_factor = 0.70
        else:
            temp_deration_factor = 0.55
        used_charge_rate = round(used_charge_rate * temp_deration_factor, 2)

        if solar_forecast_kwh < 7:
            # Extend to maximum window when solar is scarce — we need all the cheap import we can get.
            flux1_end = "05:00"
            logic_branch = "low_solar_full_window"
        else:
            # Physics-based window: charge exactly as long as needed to reach target_soc.
            energy_needed_kwh = max(0.0, (target_soc - soc) / 100.0 * battery_capacity_kwh)
            raw_minutes = (energy_needed_kwh / used_charge_rate) * 60
            # Round up to the next 15-minute slot so the window always covers the full charge need.
            quarter_slots = int((raw_minutes + 14) // 15)
            end = dt_util.now().replace(hour=2, minute=0, second=0, microsecond=0) + timedelta(minutes=quarter_slots * 15)

            # Clamp to 02:15–05:00. The 02:15 floor ensures the window is never shorter
            # than 15 minutes from the fixed 02:00 start, which isn't worth the API call.
            earliest = dt_util.now().replace(hour=2, minute=15, second=0, microsecond=0)
            latest = dt_util.now().replace(hour=5, minute=0, second=0, microsecond=0)
            if end < earliest:
                end = earliest
            if end > latest:
                end = latest

            flux1_end = end.strftime("%H:%M")
            logic_branch = "adaptive_hourly" if hourly_forecast_used else "adaptive"

        next_import_window = f"02:00→{flux1_end}"

        payload = {
            "flux_1": {
                "startTime": "02:00",
                "endTime": flux1_end,
                "targetSoc": target_soc,
            },
            "flux_2": {
                "startTime": "16:00",
                "endTime": "16:15",
                "targetSoc": 85,
            },
        }

        if dry_run:
            api_ok = None
        else:
            api_ok = await self.async_push_flux_override(payload)

        plan_state = {
            "date": dt_util.now().date().isoformat(),
            "today": today,
            "selected_full_charge_day": full_day,
            "is_full_day": is_full_day,
            "soc": soc,
            "raw_forecast_kwh": raw_forecast_kwh,
            "forecast_correction_factor": forecast_correction,
            "solar_forecast_kwh": solar_forecast_kwh,
            "forecast_band": forecast_band,
            "logic_branch": logic_branch,
            "solar_start_time": solar_start_time,
            "hours_to_solar": round(hours_to_solar, 2),
            "target_soc": target_soc,
            "target_soc_reason": soc_reason,
            "overnight_drain_adjustment": overnight_drain_adjustment,
            "overnight_drain_days": overnight_drain_days,
            "soc_adjustment": soc_adjustment,
            "soc_adjustment_days": soc_adjustment_days,
            "forecast_correction_days": forecast_correction_days,
            "effective_charge_rate_kw": effective_charge_rate,
            "used_charge_rate_kw": round(used_charge_rate, 2),
            "charge_rate_calibration_days": charge_rate_calibration_days,
            "flux1_end": flux1_end,
            "next_import_window": next_import_window,
            "payload": payload,
            "source": source,
            "api_ok": api_ok,
            "forecast_fallback": bool(forecast_fallback_note),
            "is_weekend": is_weekend,
            "avg_consumption_kw": avg_consumption_kw,
            "battery_temp_c": battery_temp_c,
            "temp_deration_factor": temp_deration_factor,
            "hourly_forecast_used": hourly_forecast_used,
            "bridge_hour": bridge_hour,
        }

        if dry_run:
            import json as _json
            await self.async_notify(
                f"🧪 Sunsynk Import Plan TEST — {plan_state['date']}",
                _json.dumps(plan_state),
            )
            return

        await self.data_logger.async_log_import_plan(plan_state)

        self.coordinator.update_state(
            current_soc_target=target_soc,
            next_import_window=next_import_window,
            last_import_plan=plan_state,
            operation_mode=self.operation_mode,
        )

        forecast_note = (
            f" (raw {round(raw_forecast_kwh, 1)} kWh ×{forecast_correction})"
            if forecast_correction != 1.0
            else ""
        )
        adjustment_parts = []
        if overnight_drain_adjustment:
            adjustment_parts.append(f"drain +{overnight_drain_adjustment}%")
        if soc_adjustment:
            adjustment_parts.append(f"eve {soc_adjustment:+d}%")
        adjustment_note = f" ({', '.join(adjustment_parts)})" if adjustment_parts else ""
        status_prefix = "" if api_ok else "⚠ API push FAILED — "
        api_note = "" if api_ok else " — inverter NOT updated; will retry next cycle."
        await self.async_notify(
            f"{status_prefix}🔋 Sunsynk Import Plan",
            (
                f"Today: {today} (Full charge: {is_full_day}). "
                f"SOC: {round(soc, 1)}%. "
                f"Solar forecast: {round(solar_forecast_kwh, 1)} kWh{forecast_note}{forecast_fallback_note}. "
                f"Import: 02:00 → {flux1_end} target {target_soc}%{adjustment_note}. "
                f"Band: {forecast_band}. Logic: {logic_branch}.{api_note}"
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
        soc_val = self._essential_state(self.battery_soc_entity)
        grid_pac_val = self._essential_state(self.grid_pac_entity)
        if soc_val is None or grid_pac_val is None:
            missing = []
            if soc_val is None:
                missing.append(self.battery_soc_entity)
            if grid_pac_val is None:
                missing.append(self.grid_pac_entity)
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
        soc = soc_val
        grid_pac = grid_pac_val
        today = dt_util.now().strftime("%A")
        is_full_day = today == self.selected_full_charge_day
        now_local = dt_util.now()

        action: dict[str, Any] = {
            "action": "none",
            "soc": soc,
            "grid_pac": grid_pac,
            "notified": False,
            "source": source,
            "reason": "no_trigger",
        }
        evening_export_disabled = False

        export_threshold = float(self.cfg[CONF_EXPORT_DISABLE_THRESHOLD])
        if (
            16 <= now_local.hour < 19
            and grid_pac > export_threshold
        ):
            payload = {
                "flux_2": {
                    "startTime": "16:00",
                    "endTime": "16:15",
                    "targetSoc": 100,
                }
            }

            api_ok = await self.async_push_flux_override(payload)

            action = {
                "action": "disable_evening_export",
                "soc": soc,
                "grid_pac": grid_pac,
                "payload": payload,
                "notified": True,
                "source": source,
                "reason": f"grid_pac_{round(grid_pac)}W_exceeds_{round(export_threshold)}W",
                "api_ok": api_ok,
            }

            evening_export_disabled = True

            self.coordinator.update_state(
                last_flux2_action=action,
                evening_export_disabled=True,
                operation_mode=self.operation_mode,
            )

            status_prefix = "" if api_ok else "⚠ API push FAILED — "
            api_note = "" if api_ok else " (inverter NOT updated)"
            await self.async_notify(
                f"{status_prefix}🏠 Flux 2 Export Disabled",
                (
                    f"Grid/load is {round(grid_pac, 0)}W between 16:00 and 19:00. "
                    f"Flux 2 export disabled by setting target SOC to 100%.{api_note}"
                ),
            )
            return

        # Trim if SOC exceeds 85% on a non-full-charge day. Target 82% leaves a 3% gap
        # below the trigger so normal fluctuation doesn't immediately re-trigger a trim.
        if not is_full_day and soc > 85 and self._cooldown_ok():
            self._mark_trim()

            trim_end = (now_local + timedelta(minutes=45)).strftime("%H:%M")

            payload = {
                "flux_2": {
                    "startTime": now_local.strftime("%H:%M"),
                    "endTime": trim_end,
                    "targetSoc": 82,
                }
            }

            api_ok = await self.async_push_flux_override(payload)

            action = {
                "action": "trim_to_82",
                "soc": soc,
                "grid_pac": grid_pac,
                "payload": payload,
                "notified": True,
                "source": source,
                "reason": f"soc_{round(soc)}%_exceeds_85",
                "api_ok": api_ok,
            }

            self.coordinator.update_state(
                last_flux2_action=action,
                evening_export_disabled=False,
                operation_mode=self.operation_mode,
            )

            status_prefix = "" if api_ok else "⚠ API push FAILED — "
            api_note = "" if api_ok else " (inverter NOT updated)"
            await self.async_notify(
                f"{status_prefix}🔋 SOC Control",
                f"SOC {round(soc, 1)}% is above 85%. Trimming to 82%.{api_note}",
            )
            return

        self.coordinator.update_state(
            last_flux2_action=action,
            evening_export_disabled=evening_export_disabled,
            operation_mode=self.operation_mode,
        )

    async def _async_initial_refresh(self, _now) -> None:
        """Populate initial state soon after startup."""
        try:
            await self.async_run_import_plan()
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Initial refresh failed")
            self.coordinator.update_state(last_error=f"Initial refresh failed: {exc}")

    async def _async_choose_best_full_charge_day(self, _now) -> None:
        """Time-change callback at 18:00 daily — only acts on Sundays."""
        if dt_util.now().strftime("%A") == "Sunday":
            await self.async_choose_best_full_charge_day()

    async def _async_run_import_plan(self, _now) -> None:
        """Time-change callback at 01:55 daily."""
        await self.async_run_import_plan()

    async def _async_periodic_flux2_check(self, _now) -> None:
        """30-minute interval callback."""
        await self.async_run_flux2_check()

    async def _async_battery_soc_changed(self, event: Event) -> None:
        """Handle SOC threshold-based reactions."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        try:
            soc = float(new_state.state)
        except (ValueError, TypeError):
            return

        if soc >= 99.5 and dt_util.now().strftime("%A") == self.selected_full_charge_day:
            if self.pending_full_trim_cancel:
                return

            async def _delayed_full_trim(_later) -> None:
                self.pending_full_trim_cancel = None
                current_soc = self._state_float(self.battery_soc_entity, 0)
                if current_soc < 99.5:
                    return
                now_local = dt_util.now()
                payload = {
                    "flux_2": {
                        "startTime": now_local.strftime("%H:%M"),
                        "endTime": (now_local + timedelta(minutes=60)).strftime("%H:%M"),
                        "targetSoc": 82,
                    }
                }
                api_ok = await self.async_push_flux_override(payload)
                self.coordinator.update_state(
                    last_flux2_action={
                        "action": "full_day_trim_to_82",
                        "soc": current_soc,
                        "grid_pac": self._state_float(self.grid_pac_entity, 0),
                        "payload": payload,
                        "notified": True,
                        "source": "automatic",
                        "reason": "held_100%_for_1h",
                        "api_ok": api_ok,
                    },
                    evening_export_disabled=False,
                    operation_mode=self.operation_mode,
                )
                status_prefix = "" if api_ok else "⚠ API push FAILED — "
                api_note = "" if api_ok else " (inverter NOT updated)"
                await self.async_notify(
                    f"{status_prefix}🔋 Full Charge Trim",
                    f"Held at 100% for 1 hour. Trimming to 82%.{api_note}",
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

        elif soc > 85 and dt_util.now().strftime("%A") != self.selected_full_charge_day:
            await self.async_run_flux2_check()

    async def _async_capture_morning_state(self, _now) -> None:
        """Capture SOC and PV power at 06:00 to measure overnight battery drain."""
        soc = self._state_float(self.battery_soc_entity, 0)
        pv_power = (
            self._state_float(self.pv_mppt0_entity, 0)
            + self._state_float(self.pv_mppt1_entity, 0)
        )
        date = dt_util.now().date().isoformat()
        await self.data_logger.async_log_morning_state(
            date=date,
            morning_soc=soc,
            morning_pv_power=pv_power,
        )
        self.coordinator.update_state(
            touch=False,
            last_morning_state={
                "type": "morning_state",
                "date": date,
                "morning_soc": round(soc, 1),
                "morning_pv_power": round(pv_power, 1),
            },
        )

    async def _async_capture_day_actuals(self, _now) -> None:
        """Capture end-of-day actuals at 22:00 and log them."""
        import json as _json
        soc = self._state_float(self.battery_soc_entity, 0)
        actual_solar_kwh = self._state_float(self.day_pv_energy_entity, 0)
        date = dt_util.now().date().isoformat()
        evening_export_disabled = self.coordinator.state.evening_export_disabled
        await self.data_logger.async_log_day_actuals(
            date=date,
            evening_soc=soc,
            actual_solar_kwh=actual_solar_kwh,
            evening_export_disabled=evening_export_disabled,
        )
        data_report_target = str(self.cfg.get(CONF_DATA_REPORT_TARGET, "")).strip()
        if data_report_target:
            plan_rec = self.coordinator.state.last_import_plan or {}
            morning_rec = self.coordinator.state.last_morning_state or {}
            actuals_rec = {
                "type": "day_actuals",
                "date": date,
                "evening_soc": round(soc, 1),
                "actual_solar_kwh": round(actual_solar_kwh, 2),
                "evening_export_disabled": evening_export_disabled,
            }
            lines = "\n".join(
                _json.dumps(r)
                for r in [plan_rec, morning_rec, actuals_rec]
                if r
            )
            await self.async_notify(
                f"Sunsynk Daily Data — {date}",
                lines,
                target=data_report_target,
            )
