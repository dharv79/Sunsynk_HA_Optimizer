# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Core optimizer logic."""
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import (
    datetime,
    timedelta,
)
from types import MappingProxyType
from typing import (
    Any,
    Callable,
    Mapping,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from .const import (
    DOMAIN,
    CONF_BATTERY_CAPACITY,
    CONF_CHARGES,
    CONF_DEFAULT_FULL_CHARGE_DAY,
    CONF_FLUX_PRODUCTS,
    CONF_INVERTER_SERIAL,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TARGET,
    CONF_OPERATION_MODE,
    CONF_PLANT_ID,
    CONF_SOLAR_FORECAST_SENSOR,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_OPERATION_MODE,
    FULL_CHARGE_DAY_OPTIONS,
)
from .data_logger import DataLogger
from .flux_helpers import (
    apply_flux_override,
    build_payload,
    merge_entry_data,
)
from .planning import (
    pack_lines,
    DYNAMIC_SHADOW_MINUTE,
    capacity_estimate_pending,
    STARTUP_PLAN_SOURCE,
    CHARGE_WATCHDOG_MINUTES,
    DRIFT_CHECK_INTERVAL_HOURS,
    clip_text,
    NOTIFICATION_KEEP_CHARS,
    config_time_problems,
)
from .opt_common import (
    _INITIAL_REFRESH_MAX_RETRIES,
    _UNAVAILABLE_STATES,
)
from .opt_overnight import OvernightMixin
from .opt_daytime import DaytimeMixin
from .opt_capture import CaptureMixin
from .opt_reports import ReportsMixin
from .opt_monitor import MonitorMixin
from .opt_events import EventsMixin
from .opt_shadow import ShadowMixin

_LOGGER = logging.getLogger(__name__)


class SunsynkOptimizer(OvernightMixin, DaytimeMixin, CaptureMixin, ReportsMixin, MonitorMixin, EventsMixin, ShadowMixin):
    """Implements optimizer behaviour.

    Construction, listener registration, config cache, notify and push helpers
    live here; the rest is split into the mixins in opt_*.py.
    """

    battery_soc_entity = property(lambda self: self._solarsynk_entity("battery_soc"))
    grid_pac_entity = property(lambda self: self._solarsynk_entity("grid_pac"))
    day_pv_energy_entity = property(lambda self: self._solarsynk_entity("pv_etoday"))
    pv_mppt0_entity = property(lambda self: self._solarsynk_entity("pv_mppt0_power"))
    pv_mppt1_entity = property(lambda self: self._solarsynk_entity("pv_mppt1_power"))
    battery_temp_entity = property(lambda self: self._solarsynk_entity("battery_temperature"))
    grid_voltage_entity = property(lambda self: self._solarsynk_entity("grid_phase0_voltage"))
    load_power_entity = property(lambda self: self._solarsynk_entity("load_total_power"))
    day_load_entity = property(lambda self: self._solarsynk_entity("load_daily_used"))
    day_grid_import_entity = property(lambda self: self._solarsynk_entity("grid_etoday_from"))
    day_grid_export_entity = property(lambda self: self._solarsynk_entity("grid_etoday_to"))

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
        # Phase 29: today's clipped-solar tally; unpersisted by design (a restart loses a partial day).
        self._clipped: dict[str, Any] = {}
        self._hourly: dict[str, Any] = {}  # phase 86: unpersisted timed samples of the day's meters
        self._derated: dict[str, Any] = {}  # phase 83: unpersisted minutes of suspected voltage derating
        # Phase 49 per-string PV energy sampled 10:00-15:00 (date, m0, m1 kWh). Unpersisted:
        # a restart only thins the day's sample; the share is a ratio so it stays valid.
        self._strings: dict[str, Any] = {}
        self._over_limit: dict[str, Any] = {}
        self._import_causes: dict[str, Any] = {}
        # Phase 55: learned 16:00-22:00 load (kW), refreshed by the 01:55 plan; unpersisted.
        self._evening_load_kw: float | None = None
        # Phase 71: learned half-hourly load profile (refreshed with the plan) and today's outside-temp samples.
        self._load_profile: dict[str, Any] | None = None
        # Phase 77: the flex event already logged ("date HH:MM"); unpersisted, the record dedup covers restarts.
        self._flex_event_logged: str | None = None
        self._outside_temp: dict[str, Any] = {}
        # Phase 40: today's daytime grid_pac samples and whether it alerted; unpersisted.
        self._daytime_import: dict[str, Any] = {}
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
        # SOC samples for the live sell-start watch (charge trend); unpersisted by design.
        self._free_event_soc_samples: list[tuple[datetime, float]] = []
        # Gentler charging (phase 23) 05:00 restore handle; re-armed from
        # coordinator.state.gentle_charge on restart.
        self._gentle_restore_unsub = None
        # Charge watchdog (phase 22) 15-minute re-check handle. Not persisted:
        # a restart mid-check just skips that night's re-check.
        self._charge_watchdog_recheck = None
        # Phase 32 failed-plan-push retry handle (also the unavailable-SOC
        # re-plan). Not persisted: a restart runs the startup plan push instead.
        self._plan_retry = None
        # Last numeric battery SOC and when it was read (UTC), for meter
        # snapshots taken while the sensor is briefly unavailable. Unpersisted.
        self._last_valid_soc: tuple[float, datetime] | None = None
        # Phase 31 drift check 10 min after a push; None = the plant income GET
        # is untested, False = it returned no fluxProducts (Flux check disabled
        # until restart). Both unpersisted.
        self._drift_check_unsub = None
        self._flux_read_supported: bool | None = None
        # Today's 16:00 peak export decision (phase 17). Not persisted: after a
        # restart mid-window a trim may replace a live export window, and the
        # 22:00 bundle omits the line (the JSONL record survives).
        # Today's peak-cover state (phase 30): date, logged/pushed flags and the
        # record. Not persisted: after a restart it re-evaluates (one push a day
        # is re-allowed, harmless — the slot is idempotent).
        self._peak_cover: dict[str, Any] | None = None
        self._peak_cover_startup_attempts = 0
        # Phase 90: one Flux 2 decide-and-push at a time (never held across a notification).
        self._flux2_lock = asyncio.Lock()
        self.data_logger = DataLogger(hass)
        self._cfg_cache: tuple | None = None  # (id key, read-only config, data, options): refs keep ids valid
        self._band_price_cache: dict[tuple, dict[str, float | None]] = {}

    @property
    def cfg(self) -> Mapping[str, Any]:
        """Merged config (entry.data + entry.options, options win), read-only and cached (phase 67).

        Rebuilt only when the entry's data/options objects are replaced; an options change
        reloads the entry anyway. Read-only, so callers cannot mutate the shared copy."""
        key = (id(self.entry.data), id(self.entry.options))
        if self._cfg_cache is None or self._cfg_cache[0] != key:
            merged = merge_entry_data(dict(self.entry.data), dict(self.entry.options))
            self._cfg_cache = (key, MappingProxyType(merged), self.entry.data, self.entry.options)
        return self._cfg_cache[1]

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

    def _check_config_times(self) -> None:
        """Phase 93: one Repairs issue while a saved tariff or Flux time is not HH:MM."""
        cfg = self.cfg
        problems = config_time_problems(cfg.get(CONF_CHARGES), cfg.get(CONF_FLUX_PRODUCTS))
        if problems:
            _LOGGER.warning("Tariff/Flux times are not HH:MM: %s", ", ".join(problems))
            ir.async_create_issue(
                self.hass, DOMAIN, "config_bad_time", is_fixable=False,
                severity=ir.IssueSeverity.ERROR, translation_key="config_bad_time",
                translation_placeholders={"items": ", ".join(problems[:6])},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "config_bad_time")

    async def async_setup(self) -> None:
        """Create listeners."""
        self._check_config_times()
        daily = (
            (18, 0, self._async_choose_best_full_charge_day),  # gated to Sundays in the handler
            (1, 55, self._async_run_import_plan),
            (CHARGE_WATCHDOG_MINUTES // 60, CHARGE_WATCHDOG_MINUTES % 60, self._async_charge_watchdog),
            (6, 0, self._async_capture_morning_state),
            (6, 5, self._async_spare_solar_morning),
            (6, 10, self._async_tariff_price_check),
            (6, 20, self._async_data_audit),  # phase 45
            (22, 0, self._async_capture_day_actuals),  # also takes the 22:00 meter snapshot
            # Phase 14 KPI snapshots at the Flux band edges.
            (2, 0, self._async_meter_snapshot),
            (5, 0, self._async_meter_snapshot),
            (16, 0, self._async_meter_snapshot),
            (16, 0, self._async_peak_export),
            (17, 0, self._async_peak_export_recheck),
            (18, 0, self._async_peak_export_recheck),
            (19, 0, self._async_meter_snapshot),
            (DYNAMIC_SHADOW_MINUTE // 60, DYNAMIC_SHADOW_MINUTE % 60, self._async_dynamic_tariff_shadow),  # phase 76
        )
        for hour, minute, callback in daily:
            self.unsubs.append(
                async_track_time_change(self.hass, callback, hour=hour, minute=minute, second=0)
            )
        self.unsubs.append(
            async_track_time_interval(self.hass, self._async_periodic_flux2_check, timedelta(minutes=30))
        )
        self.unsubs.append(
            async_track_time_interval(
                self.hass, self._async_drift_check, timedelta(hours=DRIFT_CHECK_INTERVAL_HOURS)
            )
        )
        self.unsubs.append(
            async_track_state_change_event(self.hass, [self.battery_soc_entity], self._async_battery_soc_changed)
        )
        # Phase 48: grid outage start/restore from the grid voltage. Missing entity just never fires.
        self.unsubs.append(
            async_track_state_change_event(self.hass, [self.grid_voltage_entity], self._async_grid_status_changed)
        )
        self.hass.async_create_task(self._async_grid_reconcile())

        if self.coordinator.state.selected_full_charge_day is None:
            self.coordinator.update_state(
                selected_full_charge_day=self.cfg[CONF_DEFAULT_FULL_CHARGE_DAY]
            )

        self.coordinator.update_state(operation_mode=self.operation_mode)
        self.unsubs.append(async_call_later(self.hass, 60, self._async_initial_refresh))
        self.unsubs.append(async_call_later(self.hass, 90, self._async_spare_solar_startup))
        if capacity_estimate_pending(self.coordinator.state.battery_capacity_estimate):
            self.unsubs.append(async_call_later(self.hass, 120, self._async_initial_capacity_estimate))

        if self._free_event_active():
            self._arm_free_event_timers(self.coordinator.state.free_event)
        if self.coordinator.state.gentle_charge.get("phase") == "applied":
            self._arm_gentle_restore()

    async def async_shutdown(self) -> None:
        for unsub in self.unsubs:
            unsub()
        self.unsubs.clear()
        self._clear_free_event_timers()
        if self._gentle_restore_unsub:
            self._gentle_restore_unsub()
            self._gentle_restore_unsub = None
        if self._charge_watchdog_recheck:
            self._charge_watchdog_recheck()
            self._charge_watchdog_recheck = None
        self._cancel_plan_retry()
        if self._drift_check_unsub:
            self._drift_check_unsub()
            self._drift_check_unsub = None
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

    def _capacity_kwh(self, cfg: dict[str, Any] | None = None) -> float:
        """Configured battery capacity (kWh), floored so it never divides by zero."""
        return max(0.1, float((cfg if cfg is not None else self.cfg).get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY)))

    def _hourly_or_synthetic_forecast(self, cfg: dict[str, Any], now: datetime) -> dict[int, float] | None:
        """The hourly forecast sensor, else a sine profile from the raw daily forecast."""
        hourly = self._get_hourly_forecast_kwh()
        if hourly is None:
            raw_daily, _ = self._resolve_raw_forecast(cfg[CONF_SOLAR_FORECAST_SENSOR], now)
            if raw_daily is not None:
                hourly = self._synthetic_hourly_forecast(raw_daily)
        return hourly

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

    async def async_notify_debug(self, title: str, message: str, target: str | None = None) -> None:
        """Debug-stream send: whole JSON lines only, split across messages when long (phase 89)."""
        packs = pack_lines(message.split("\n"))
        for i, pack in enumerate(packs, 1):
            suffix = f" ({i}/{len(packs)})" if len(packs) > 1 else ""
            await self.async_notify(title + suffix, "\n".join(pack), target=target)

    async def async_notify(self, title: str, message: str, target: str | None = None) -> None:
        """Send a notification via the configured HA notify service."""
        cfg = self.cfg
        service_string = self._cfg_str(CONF_NOTIFY_SERVICE, cfg)
        # Stored state keeps only the start of the text: a debug bundle is several kB (phase 92).
        record: dict[str, Any] = {
            "ok": False, "service": service_string, "title": title,
            "message": clip_text(message, NOTIFICATION_KEEP_CHARS), "message_chars": len(message),
        }
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
            last_pushed_flux=deepcopy(payload.get("fluxProducts") or []),
        )
        self._schedule_drift_check()
        return True

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

        Skips the one-shot plan if a reload lands in 16:00-19:00: the plan
        re-pushes flux_2 to its default (targetSoc 85), which would undo an
        export pause or a live peak export (unpersisted, so unknown after a
        reload). The nightly 01:55 plan re-plans normally.
        """
        now = dt_util.now()
        if self._slots_held():
            _LOGGER.info("Skipping initial import plan — %s holds the Flux slots", self._slots_held_by())
            return
        if 16 <= now.hour < 19:
            # Its default Flux 2 slot would undo an export pause or a live peak
            # export (unpersisted, so unknown after a reload); 01:55 re-plans.
            _LOGGER.info("Skipping initial import plan — inside the 16:00-19:00 peak window")
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
        # The startup plan resets Flux 1 to the overnight window, wiping a
        # peak cover top-up (and its unpersisted state) pushed before the
        # restart; re-check now rather than wait for the 30-minute tick,
        # which may not come before 16:00. No-op outside 09:00-16:00.
        await self._async_peak_cover_after_startup()
        await self._guarded(self._async_monthly_reports, "Monthly reports")

    def _read_soc(self) -> float | None:
        """Live battery SOC (None when unavailable); remembers the last numeric reading."""
        soc = self._essential_state(self.battery_soc_entity)
        if soc is not None:
            self._last_valid_soc = (soc, dt_util.utcnow())
        return soc

    async def _async_write_setting(
        self, key: str, value: Any, write_if: Callable[[float], bool] | None = None
    ) -> tuple[bool | None, float | None]:
        """Read the inverter settings, change one key, write them all back.

        The settings-endpoint twin of _async_post_with_status (do-not-break 5):
        surfaces failures via last_api_result / last_error and returns
        (ok, previous value). Refuses to write when the current value can't be
        read as a number, so there is always something to restore. When
        `write_if(previous)` is False nothing is written: (None, previous).
        """
        serial = self.inverter_serial
        try:
            settings = await self.coordinator.api.async_read_settings(serial)
            previous = float(settings[key])
            if write_if is not None and not write_if(previous):
                return None, previous
            await self.coordinator.api.async_write_settings(serial, {**settings, key: str(value)})
        except Exception as exc:  # pragma: no cover
            _LOGGER.exception("Sunsynk settings write failed (%s)", key)
            self.coordinator.update_state(
                last_api_result={"ok": False, "error": str(exc)},
                last_error=f"Settings write failed ({key}): {exc}",
            )
            return False, None
        self.coordinator.update_state(last_api_result={"ok": True, "setting": key, "value": value})
        self._schedule_drift_check()
        return True, previous
