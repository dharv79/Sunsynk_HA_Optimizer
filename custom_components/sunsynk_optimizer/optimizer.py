"""Core optimizer logic."""

from __future__ import annotations

import hashlib
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

from .flux_helpers import apply_flux_override, build_payload, default_flux_products, merge_entry_data
from .const import (
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
    FULL_CHARGE_DAY_OPTIONS,
)

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

    @property
    def cfg(self) -> dict[str, Any]:
        return merge_entry_data(dict(self.entry.data), dict(self.entry.options))

    @property
    def operation_mode(self) -> str:
        return str(self.cfg.get(CONF_OPERATION_MODE, "auto"))

    @property
    def plant_id(self) -> str:
        return str(self.cfg[CONF_PLANT_ID]).strip()

    @property
    def inverter_serial(self) -> str:
        return str(self.cfg[CONF_INVERTER_SERIAL]).strip()

    @property
    def battery_soc_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_battery_soc"

    @property
    def grid_pac_entity(self) -> str:
        return f"sensor.solarsynkv3_{self.inverter_serial}_grid_pac"

    @property
    def selected_full_charge_day(self) -> str:
        state_day = self.coordinator.state.selected_full_charge_day
        if state_day in FULL_CHARGE_DAY_OPTIONS:
            return state_day
        return self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY]

    async def async_setup(self) -> None:
        self.unsubs.append(
            async_track_time_change(
                self.hass, self._async_choose_best_full_charge_day, hour=18, minute=0, second=0
            )
        )
        self.unsubs.append(
            async_track_time_change(
                self.hass, self._async_run_import_plan, hour=1, minute=55, second=0
            )
        )
        self.unsubs.append(
            async_track_time_interval(
                self.hass, self._async_periodic_flux2_check, timedelta(minutes=30)
            )
        )
        self.unsubs.append(
            async_track_state_change_event(
                self.hass, [self.battery_soc_entity], self._async_battery_soc_changed
            )
        )

        if self.coordinator.state.selected_full_charge_day is None:
            self.coordinator.update_state(
                selected_full_charge_day=self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY],
                operation_mode=self.operation_mode,
            )

        self.unsubs.append(async_call_later(self.hass, 15, self._async_initial_refresh))

    async def async_shutdown(self) -> None:
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
        if self.pending_full_trim_cancel:
            self.pending_full_trim_cancel()
            self.pending_full_trim_cancel = None

    def _state_float(self, entity_id: str, default: float = 0.0) -> float:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except ValueError:
            return default

    def _cooldown_ok(self, seconds: int = 1800) -> bool:
        if self.last_trim_ts is None:
            return True
        return (dt_util.utcnow().timestamp() - self.last_trim_ts) > seconds

    def _mark_trim(self) -> None:
        self.last_trim_ts = dt_util.utcnow().timestamp()

    def _payload_hash(self, payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def async_notify(self, title: str, message: str) -> None:
        service_string = self.cfg[CONF_NOTIFY_SERVICE]
        notify_target = self.cfg.get(CONF_NOTIFY_TARGET, "")
        try:
            domain, service = service_string.split(".", 1)
        except ValueError:
            self.coordinator.update_state(last_error=f"Invalid notify service: {service_string}")
            return

        data: dict[str, Any] = {"title": title, "message": message}
        if notify_target:
            data["target"] = [notify_target]

        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
            self.coordinator.update_state(
                last_notification={"title": title, "message": message},
                touch=False,
            )
        except Exception as err:
            _LOGGER.warning("Notification failed via %s: %s", service_string, err)
            self.coordinator.update_state(last_error=f"Notify failed: {err}")

    async def _async_post_payload(
        self,
        payload: dict[str, Any],
        action_name: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        payload_hash = self._payload_hash(payload)

        if not force and self.coordinator.state.last_payload_hash == payload_hash:
            state = {
                "action": f"{action_name}_skipped",
                "reason": "duplicate_payload",
                "payload": payload,
            }
            self.coordinator.update_state(last_api_result=state, last_error=None, touch=False)
            return state

        if self.operation_mode == "monitor":
            state = {"action": f"{action_name}_monitor_only", "payload": payload}
            self.coordinator.update_state(
                last_api_result=state,
                last_error=None,
                last_payload_hash=payload_hash,
                touch=False,
            )
            return state

        result = await self.coordinator.api.async_post_income(self.plant_id, payload)
        state = {"action": action_name, "payload": payload, "api_result": result}
        self.coordinator.update_state(
            last_api_result=state,
            last_error=None,
            last_payload_hash=payload_hash,
            touch=False,
        )
        return state

    async def async_push_current_config(self, *, force: bool = False) -> dict[str, Any]:
        payload = build_payload(self.cfg)
        return await self._async_post_payload(payload, "push_current_config", force=force)

    async def async_push_flux_override(
        self,
        data: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        config = self.cfg
        flux_products = apply_flux_override(
            config[CONF_FLUX_PRODUCTS],
            flux_1=data.get("flux_1"),
            flux_2=data.get("flux_2"),
        )
        payload = build_payload(config, flux_products=flux_products)
        return await self._async_post_payload(payload, "push_flux_override", force=force)

    async def async_reset_flux_defaults(self) -> dict[str, Any]:
        config = self.cfg.copy()
        config[CONF_FLUX_PRODUCTS] = default_flux_products()
        payload = build_payload(config)
        return await self._async_post_payload(payload, "reset_flux_defaults", force=True)

    async def async_reset_flux_baseline(self) -> dict[str, Any]:
        payload = build_payload(self.cfg, flux_products=default_flux_products())
        result = await self._async_post_payload(payload, "reset_flux_baseline", force=True)
        self.coordinator.update_state(
            last_flux2_action={
                "action": "baseline_restored",
                "payload": {"flux_2": default_flux_products()[1]},
            },
            evening_export_disabled=False,
            current_soc_target=85,
            touch=False,
        )
        await self.async_notify(
            "🔄 Flux baseline restored",
            "Flux 1 and Flux 2 were reset to the baseline windows.",
        )
        return result

    async def async_choose_best_full_charge_day(self) -> dict[str, Any]:
        weather_entity = self.cfg[CONF_WEATHER_ENTITY]
        response = await self.hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": weather_entity, "type": "daily"},
            blocking=True,
            return_response=True,
        )
        items = response.get(weather_entity, {}).get("forecast", []) if response else []
        scores: dict[str, float] = {day: -999 for day in FULL_CHARGE_DAY_OPTIONS}

        for raw in items:
            dt_raw = raw.get("datetime")
            if not dt_raw:
                continue
            dt = dt_util.parse_datetime(dt_raw)
            if dt is None:
                continue
            day_name = dt.strftime("%A")
            if day_name not in scores:
                continue

            cond = raw.get("condition", "unknown")
            cloud = float(raw.get("cloud_coverage", 50) or 50)
            rain = float(raw.get("precipitation_probability", 0) or 0)
            temp = float(raw.get("temperature", 15) or 15)

            score = 100 - cloud - (rain * 0.7)
            if cond in ["sunny", "clear"]:
                score += 25
            elif cond in ["partlycloudy"]:
                score += 10
            elif cond in ["cloudy", "fog"]:
                score -= 10
            elif cond in ["rainy", "pouring", "lightning-rainy", "snowy", "snowy-rainy"]:
                score -= 25

            if temp >= 18:
                score += 3
            elif temp <= 5:
                score -= 3
            if day_name == "Thursday":
                score -= 5
            elif day_name == "Friday":
                score -= 15

            scores[day_name] = round(score, 1)

        best_day = max(scores, key=scores.get)
        result = {"best_day": best_day, "scores": scores}
        self.coordinator.update_state(
            selected_full_charge_day=best_day,
            last_full_charge_scores=scores,
            last_error=None,
            operation_mode=self.operation_mode,
        )
        await self.async_notify(
            "🔋 Sunsynk Full Charge Day Updated",
            f"Chosen day: {best_day}. Scores: {scores}",
        )
        return result

    async def async_run_import_plan(self) -> dict[str, Any]:
        now = dt_util.now()
        soc = self._state_float(self.battery_soc_entity)
        today = now.strftime("%A")
        is_full_day = today == self.selected_full_charge_day
        solar_forecast_kwh = self._state_float(self.cfg[CONF_SOLAR_FORECAST_SENSOR])
        is_summer = now.month in [4, 5, 6, 7, 8, 9]
        target_soc = 100 if is_full_day else 85

        end = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if soc > 75:
            end = end.replace(hour=2, minute=30)
        elif soc > 65:
            end = end.replace(hour=3, minute=0)
        elif soc > 50:
            end = end.replace(hour=3, minute=30)

        if solar_forecast_kwh >= 12:
            end -= timedelta(minutes=60)
        elif solar_forecast_kwh < 8:
            end += timedelta(minutes=30)
        if is_summer:
            end -= timedelta(minutes=30)

        min_end = now.replace(hour=2, minute=15, second=0, microsecond=0)
        max_end = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if end < min_end:
            end = min_end
        if end > max_end:
            end = max_end

        flux1_end = end.strftime("%H:%M")
        payload = {
            "flux_1": {"startTime": "02:00", "endTime": flux1_end, "targetSoc": target_soc},
            "flux_2": {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85},
        }
        await self.async_push_flux_override(payload, force=True)

        result = {
            "today": today,
            "selected_full_charge_day": self.selected_full_charge_day,
            "is_full_day": is_full_day,
            "soc": soc,
            "solar_forecast_kwh": solar_forecast_kwh,
            "flux1_end": flux1_end,
            "target_soc": target_soc,
        }
        self.coordinator.update_state(
            last_import_plan=result,
            last_error=None,
            next_import_window=f"02:00→{flux1_end}",
            current_soc_target=target_soc,
        )
        await self.async_notify(
            "🔋 Sunsynk Import Plan",
            f"Today: {today}. Full charge: {is_full_day}. SOC: {soc}%. "
            f"Solar forecast: {solar_forecast_kwh} kWh. Import 02:00 → {flux1_end}, "
            f"target {target_soc}%.",
        )
        return result

    async def async_run_flux2_check(self) -> dict[str, Any]:
        now = dt_util.now()
        soc = self._state_float(self.battery_soc_entity)
        grid_pac = self._state_float(self.grid_pac_entity)
        is_full_day = now.strftime("%A") == self.selected_full_charge_day
        hour = now.hour
        threshold = float(self.cfg[CONF_EXPORT_DISABLE_THRESHOLD])

        action = "none"
        payload: dict[str, Any] | None = None
        evening_export_disabled = False
        target = 85

        if 16 <= hour < 19 and grid_pac > threshold:
            payload = {
                "flux_2": {"startTime": "16:00", "endTime": "16:15", "targetSoc": 100}
            }
            action = "disable_evening_export"
            evening_export_disabled = True
            target = 100
            if not self.coordinator.state.evening_export_disabled:
                await self.async_notify(
                    "🏠 Flux 2 Export Disabled",
                    f"Grid/load is {round(grid_pac)}W between 16:00 and 19:00. "
                    "Flux 2 export disabled by setting target SOC to 100%.",
                )
        elif not is_full_day and soc > 85 and self._cooldown_ok():
            start = now.strftime("%H:%M")
            end = (now + timedelta(minutes=45)).strftime("%H:%M")
            payload = {"flux_2": {"startTime": start, "endTime": end, "targetSoc": 82}}
            action = "trim_to_82"
            target = 82
            self._mark_trim()
            await self.async_notify("🔋 SOC Control", f"SOC {soc}% is above 85%. Trimming to 82%.")

        if payload is not None:
            await self.async_push_flux_override(payload)

        result = {
            "soc": soc,
            "grid_pac": grid_pac,
            "is_full_day": is_full_day,
            "action": action,
            "payload": payload,
        }
        self.coordinator.update_state(
            last_flux2_action=result,
            evening_export_disabled=evening_export_disabled,
            last_error=None,
            current_soc_target=target,
        )
        return result

    async def async_handle_soc_threshold(self, soc: float) -> dict[str, Any] | None:
        now = dt_util.now()
        is_full_day = now.strftime("%A") == self.selected_full_charge_day
        if soc >= 99.5 and is_full_day and self._cooldown_ok():
            self._mark_trim()
            if self.pending_full_trim_cancel:
                self.pending_full_trim_cancel()
            self.pending_full_trim_cancel = async_call_later(
                self.hass, 3600, self._async_full_trim_after_hold
            )
            return {"action": "schedule_full_trim", "soc": soc}

        if soc > 85 and not is_full_day and self._cooldown_ok():
            return await self.async_run_flux2_check()
        return None

    async def _async_full_trim_after_hold(self, _now) -> None:
        self.pending_full_trim_cancel = None
        soc = self._state_float(self.battery_soc_entity)
        if soc < 99.5:
            return
        now = dt_util.now()
        payload = {
            "flux_2": {
                "startTime": now.strftime("%H:%M"),
                "endTime": (now + timedelta(minutes=60)).strftime("%H:%M"),
                "targetSoc": 82,
            }
        }
        await self.async_push_flux_override(payload)
        result = {"action": "full_day_trim_to_82", "soc": soc, "payload": payload}
        self.coordinator.update_state(
            last_flux2_action=result,
            evening_export_disabled=False,
            last_error=None,
            current_soc_target=82,
        )
        await self.async_notify("🔋 Full Charge Trim", "Held at 100% for 1 hour. Trimming to 82%.")

    async def _async_initial_refresh(self, _now) -> None:
        try:
            if dt_util.now().strftime("%A") == "Sunday" and not self.coordinator.state.last_full_charge_scores:
                await self.async_choose_best_full_charge_day()
            await self.async_run_flux2_check()
        except Exception as err:
            _LOGGER.exception("Initial refresh failed: %s", err)
            self.coordinator.update_state(last_error=str(err))

    async def _async_choose_best_full_charge_day(self, _now) -> None:
        if dt_util.now().strftime("%A") != "Sunday":
            return
        try:
            await self.async_choose_best_full_charge_day()
        except Exception as err:
            _LOGGER.exception("Choose best full charge day failed: %s", err)
            self.coordinator.update_state(last_error=str(err))

    async def _async_run_import_plan(self, _now) -> None:
        try:
            await self.async_run_import_plan()
        except Exception as err:
            _LOGGER.exception("Import plan failed: %s", err)
            self.coordinator.update_state(last_error=str(err))

    async def _async_periodic_flux2_check(self, _now) -> None:
        try:
            await self.async_run_flux2_check()
        except Exception as err:
            _LOGGER.exception("Periodic Flux 2 check failed: %s", err)
            self.coordinator.update_state(last_error=str(err))

    async def _async_battery_soc_changed(self, event: Event) -> None:
        to_state = event.data.get("new_state")
        if to_state is None or to_state.state in ("unknown", "unavailable"):
            return
        try:
            soc = float(to_state.state)
        except ValueError:
            return

        try:
            await self.async_handle_soc_threshold(soc)
        except Exception as err:
            _LOGGER.exception("SOC threshold handling failed: %s", err)
            self.coordinator.update_state(last_error=str(err))