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
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_RATE,
    CONF_DEFAULT_FULL_CHARGE_DAY,
    CONF_EXPORT_DISABLE_THRESHOLD,
    CONF_FLUX_PRODUCTS,
    CONF_INVERTER_SERIAL,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TARGET,
    CONF_OPERATION_MODE,
    CONF_PLANT_ID,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_WEATHER_ENTITY,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGE_RATE,
    DEFAULT_OPERATION_MODE,
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

    async def async_notify(self, title: str, message: str) -> None:
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
        notify_target = str(self.cfg.get(CONF_NOTIFY_TARGET, "")).strip()
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

    async def async_push_current_config(self) -> None:
        """Push the baseline Flux config from settings to the Sunsynk API without any overrides."""
        config = self.cfg
        payload = build_payload(config)
        result = await self.coordinator.api.async_post_income(self.plant_id, payload)
        self.coordinator.update_state(last_api_result=result)

    async def async_push_flux_override(self, payload: dict[str, Any]) -> None:
        """Merge a Flux 1/2 override dict onto the config baseline and push to the API."""
        config = self.cfg
        flux_products = apply_flux_override(
            config.get(CONF_FLUX_PRODUCTS, []),
            payload.get("flux_1"),
            payload.get("flux_2"),
        )
        full_payload = build_payload(config, flux_products)
        result = await self.coordinator.api.async_post_income(self.plant_id, full_payload)
        self.coordinator.update_state(last_api_result=result)

    async def async_reset_flux_baseline(self) -> None:
        config = self.cfg
        baseline = config.get(CONF_FLUX_PRODUCTS, [])
        payload = build_payload(config, baseline)
        result = await self.coordinator.api.async_post_income(self.plant_id, payload)
        self.coordinator.update_state(
            last_api_result=result,
            last_flux2_action={"action": "reset_baseline", "payload": payload, "notified": True},
            evening_export_disabled=False,
        )
        await self.async_notify(
            "🔋 Sunsynk baseline restored",
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

    async def async_run_import_plan(self) -> None:
        """Calculate and push overnight import plan."""
        if self.operation_mode == "monitor":
            self.coordinator.update_state(
                operation_mode="monitor",
                last_import_plan={"logic_branch": "monitor_only"},
            )
            return

        soc = self._state_float(self.battery_soc_entity, 0)
        today = dt_util.now().strftime("%A")
        full_day = self.selected_full_charge_day
        is_full_day = today == full_day
        forecast_entity = self.cfg[CONF_SOLAR_FORECAST_SENSOR]
        raw_forecast_kwh = self._state_float(forecast_entity, 0)
        battery_capacity_kwh = float(self.cfg.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY))
        charge_rate_kw = float(self.cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))

        paired_days = await self.data_logger.async_load_paired_days(days=30)
        forecast_correction = self.data_logger.compute_forecast_correction(paired_days)
        solar_forecast_kwh = round(raw_forecast_kwh * forecast_correction, 2)
        forecast_band = self._forecast_band(solar_forecast_kwh)

        # SOC target tiers — chosen to balance overnight grid cost against solar recovery risk.
        if is_full_day:
            target_soc = 100
            soc_reason = "weekly_full_charge_day"
        else:
            if solar_forecast_kwh < 7:
                # Very low solar — fill up regardless of band to ensure enough energy.
                if forecast_band == "winter_like":
                    target_soc = 100
                    soc_reason = "low_solar_override_winter_like"
                else:
                    target_soc = 95
                    soc_reason = "low_solar_override"
            elif forecast_band == "winter_like":
                target_soc = 95   # limited solar → stay high to cover the day
                soc_reason = "winter_like"
            elif forecast_band == "summer_like":
                target_soc = 80   # plenty of solar → leave headroom for PV absorption
                soc_reason = "summer_like"
            else:
                target_soc = 85   # shoulder — mid point
                soc_reason = "shoulder"

        overnight_drain_adjustment = 0
        soc_adjustment = 0
        if not is_full_day:
            overnight_drain_adjustment = self.data_logger.compute_overnight_drain_adjustment(
                paired_days
            )
            soc_adjustment = self.data_logger.compute_soc_target_adjustment(
                paired_days, forecast_band
            )
            target_soc = max(50, min(100, target_soc + overnight_drain_adjustment + soc_adjustment))

        if solar_forecast_kwh < 7:
            # Extend to maximum window when solar is scarce — we need all the cheap import we can get.
            flux1_end = "05:00"
            logic_branch = "low_solar_full_window"
        else:
            # Physics-based window: charge exactly as long as needed to reach target_soc.
            energy_needed_kwh = max(0.0, (target_soc - soc) / 100.0 * battery_capacity_kwh)
            raw_minutes = (energy_needed_kwh / charge_rate_kw) * 60
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
            logic_branch = "adaptive"

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

        await self.async_push_flux_override(payload)

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
            "target_soc": target_soc,
            "target_soc_reason": soc_reason,
            "overnight_drain_adjustment": overnight_drain_adjustment,
            "soc_adjustment": soc_adjustment,
            "flux1_end": flux1_end,
            "next_import_window": next_import_window,
            "payload": payload,
        }

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
        await self.async_notify(
            "🔋 Sunsynk Import Plan",
            (
                f"Today: {today} (Full charge: {is_full_day}). "
                f"SOC: {round(soc, 1)}%. "
                f"Solar forecast: {round(solar_forecast_kwh, 1)} kWh{forecast_note}. "
                f"Import: 02:00 → {flux1_end} target {target_soc}%{adjustment_note}. "
                f"Band: {forecast_band}. Logic: {logic_branch}."
            ),
        )

    async def async_run_flux2_check(self) -> None:
        """Run Flux 2 evening export / trim logic."""
        if self.operation_mode == "monitor":
            self.coordinator.update_state(
                operation_mode="monitor",
                last_flux2_action={"action": "monitor_only", "notified": False},
            )
            return

        soc = self._state_float(self.battery_soc_entity, 0)
        grid_pac = self._state_float(self.grid_pac_entity, 0)
        today = dt_util.now().strftime("%A")
        is_full_day = today == self.selected_full_charge_day
        now_local = dt_util.now()

        action: dict[str, Any] = {
            "action": "none",
            "soc": soc,
            "grid_pac": grid_pac,
            "notified": False,
        }
        evening_export_disabled = False

        if (
            16 <= now_local.hour < 19
            and grid_pac > float(self.cfg[CONF_EXPORT_DISABLE_THRESHOLD])
        ):
            payload = {
                "flux_2": {
                    "startTime": "16:00",
                    "endTime": "16:15",
                    "targetSoc": 100,
                }
            }

            await self.async_push_flux_override(payload)

            action = {
                "action": "disable_evening_export",
                "soc": soc,
                "grid_pac": grid_pac,
                "payload": payload,
                "notified": True,
            }

            evening_export_disabled = True

            self.coordinator.update_state(
                last_flux2_action=action,
                evening_export_disabled=True,
                operation_mode=self.operation_mode,
            )

            await self.async_notify(
                "🏠 Flux 2 Export Disabled",
                (
                    f"Grid/load is {round(grid_pac, 0)}W between 16:00 and 19:00. "
                    "Flux 2 export disabled by setting target SOC to 100%."
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

            await self.async_push_flux_override(payload)

            action = {
                "action": "trim_to_82",
                "soc": soc,
                "grid_pac": grid_pac,
                "payload": payload,
                "notified": True,
            }

            self.coordinator.update_state(
                last_flux2_action=action,
                evening_export_disabled=False,
                operation_mode=self.operation_mode,
            )

            await self.async_notify(
                "🔋 SOC Control",
                f"SOC {round(soc, 1)}% is above 85%. Trimming to 82%.",
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
                await self.async_push_flux_override(payload)
                self.coordinator.update_state(
                    last_flux2_action={
                        "action": "full_day_trim_to_82",
                        "soc": current_soc,
                        "grid_pac": self._state_float(self.grid_pac_entity, 0),
                        "payload": payload,
                        "notified": True,
                    },
                    evening_export_disabled=False,
                    operation_mode=self.operation_mode,
                )
                await self.async_notify(
                    "🔋 Full Charge Trim",
                    "Held at 100% for 1 hour. Trimming to 82%.",
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
        await self.data_logger.async_log_morning_state(
            date=dt_util.now().date().isoformat(),
            morning_soc=soc,
            morning_pv_power=pv_power,
        )

    async def _async_capture_day_actuals(self, _now) -> None:
        """Capture end-of-day actuals at 22:00 and log them."""
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
