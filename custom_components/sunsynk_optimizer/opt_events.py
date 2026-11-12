# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: free electricity events and flex events, plus the service entry points."""
from __future__ import annotations

import json
from datetime import (
    datetime,
    timedelta,
)
from typing import Any
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from .const import (
    CONF_CHARGE_RATE,
    CONF_CHARGES,
    CONF_DATA_REPORT_TARGET,
    CONF_FREE_EVENT_CHARGE_RATE_KW,
    CONF_FREE_EVENT_EXPORT_RATE_KW,
    DEFAULT_CHARGE_RATE,
)
from .flux_helpers import band_price_pence_per_kwh
from .planning import (
    event_probe_paths,
    next_plan_date,
    manual_free_event_error,
    free_event_result,
    plan_free_event,
    FREE_EVENT_WATCH_MINUTES,
    free_event_sell_decision,
    elapsed_seconds,
    free_event_in_watch,
    free_event_watch_from,
    soc_trend_kw,
    restore_flux2_slot,
)
from .opt_common import (
    _api_note,
    _kwh,
    _pct,
)

class EventsMixin:
    """Free electricity events and flex events, plus the service entry points (methods of SunsynkOptimizer)."""

    def _event_rates_kw(self, cfg: dict[str, Any]) -> tuple[float, float]:
        """(charge, export) kW for free events; blank = the charge rate."""
        default_rate = float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE))
        return (
            float(cfg.get(CONF_FREE_EVENT_CHARGE_RATE_KW) or default_rate),
            float(cfg.get(CONF_FREE_EVENT_EXPORT_RATE_KW) or default_rate),
        )

    def _service_guard(self, write: bool = True) -> None:
        """Refuse a service write in monitor mode or while an event holds the slots (rules 7 and 16)."""
        if not write:
            return
        if self.operation_mode == "monitor":
            raise HomeAssistantError("Monitor mode: the optimizer makes no changes to the inverter")
        if self._slots_held():
            raise HomeAssistantError("A free event is holding the inverter slots")

    async def async_service_override(self, soc: int | None = None, skip: bool = False) -> dict[str, Any]:
        """Service: set or skip the next 01:55 plan's overnight target (phase 46). Nothing is pushed now."""
        self._service_guard()
        if not skip and soc is None:
            raise HomeAssistantError("Give a target SOC or skip")
        plan_date = next_plan_date(dt_util.now()).isoformat()
        override = {"date": plan_date, **({"skip": True} if skip else {"soc": int(soc)})}
        self.coordinator.update_state(user_override=override)
        return override

    async def async_service_schedule_free_event(self, start: datetime, end: datetime) -> bool:
        self._service_guard()
        return await self.async_schedule_free_event(dt_util.as_local(start), dt_util.as_local(end), "service")

    async def async_service_cancel_free_event(self) -> None:
        await self.async_cancel_free_event()

    async def async_service_probe_events(self) -> dict[str, Any]:
        """Service: probe the Sunsynk cloud's alarm/event routes (phase 43), read-only. Reply and debug stream."""
        now = dt_util.now()
        paths = event_probe_paths(
            self.inverter_serial, self.plant_id, now.strftime("%Y-%m-%d"), (now - timedelta(days=30)).strftime("%Y-%m-%d")
        )
        results = await self.coordinator.api.async_probe_events(paths)
        target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if target:
            await self.async_notify_debug(
                "🔋 Sunsynk: events probe", json.dumps({"type": "events_probe", "results": results}), target=target
            )
        return {"results": results}

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

        if phase == "scheduled":
            # The sell start is decided live from the SOC and its trend (_async_do_free_event_watch).
            watch_from = free_event_watch_from(free_start)
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, elapsed_seconds(watch_from, now)), self._async_free_event_watch_start)
            )
        if phase in ("scheduled", "selling"):
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, elapsed_seconds(free_start, now)), self._async_free_event_free_start)
            )
        if phase in ("scheduled", "selling", "charging"):
            self._free_event_unsubs.append(
                async_call_later(self.hass, max(0, elapsed_seconds(free_end, now)), self._async_free_event_free_end)
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
        if self._free_event_active():
            await self.async_notify(
                "⚠️ Sunsynk: free event NOT scheduled",
                "A free event is already scheduled or in progress. Cancel it first.",
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
        battery_capacity_kwh = self._capacity_kwh(cfg)
        charge_rate_kw, export_rate_kw = self._event_rates_kw(cfg)

        result = plan_free_event(free_start, free_end, soc, battery_capacity_kwh, charge_rate_kw, export_rate_kw)
        watch_from = free_event_watch_from(free_start)

        # The sell start and kWh are decided live from watch_from, so no estimate is stored.
        event = {
            "source": source,
            "free_start": free_start.isoformat(),
            "free_end": free_end.isoformat(),
            "watch_from": watch_from.isoformat(),
            "sell_floor_soc": result.sell_floor_soc,
            "expected_refill_kwh": result.expected_refill_kwh,
            "charge_rate_kw": charge_rate_kw,
            "export_rate_kw": export_rate_kw,
            "phase": "scheduled",
        }
        self.coordinator.update_state(free_event=event)
        self._arm_free_event_timers(event)

        await self.async_notify(
            "🔋 Sunsynk: free event scheduled",
            (
                f"Free period {free_start.strftime('%Y-%m-%d %H:%M')} → {free_end.strftime('%H:%M')} ({source}). "
                f"Sell floor {result.sell_floor_soc}%, refill up to {result.expected_refill_kwh} kWh. "
                f"Watching from {watch_from.strftime('%a %H:%M')}: selling starts when the battery's "
                "level and charge rate need it."
            ),
        )
        await self.data_logger.async_log_free_event({"phase": "scheduled", **event})
        await self._async_report_event("scheduled", event)
        return True

    async def _async_free_event_watch_start(self, _now) -> None:
        """Start the live sell-start watch: check now, then every FREE_EVENT_WATCH_MINUTES."""
        self._free_event_soc_samples = []
        self._free_event_unsubs.append(
            async_track_time_interval(
                self.hass, self._async_free_event_watch, timedelta(minutes=FREE_EVENT_WATCH_MINUTES)
            )
        )
        await self._async_free_event_watch(_now)

    async def _async_free_event_watch(self, _now) -> None:
        await self._guarded(self._async_do_free_event_watch, "Free event sell watch")

    async def _async_do_free_event_watch(self) -> None:
        """Start selling once the projected SOC at the free start needs all the time left to export."""
        event = dict(self.coordinator.state.free_event)
        if event.get("phase") != "scheduled":
            return
        soc = self._essential_state(self.battery_soc_entity)
        if soc is None:
            return
        now = dt_util.now()
        free_start = dt_util.as_local(dt_util.parse_datetime(event["free_start"]))
        watch_from = dt_util.parse_datetime(event.get("watch_from") or "")
        if not free_event_in_watch(now, free_start, dt_util.as_local(watch_from) if watch_from else None):
            return
        capacity = self._capacity_kwh()
        self._free_event_soc_samples = [
            *[s for s in self._free_event_soc_samples if elapsed_seconds(now, s[0]) <= 3600], (now, soc)
        ]
        decision = free_event_sell_decision(
            soc, event["sell_floor_soc"], capacity, soc_trend_kw(self._free_event_soc_samples, capacity, now),
            event["export_rate_kw"], max(0.0, elapsed_seconds(free_start, now) / 3600),
        )
        if not decision["sell_now"]:
            return
        event.update(
            sell_start=now.isoformat(),
            expected_export_kwh=decision["energy_kwh"],
            skip_sell=False,
            sell_trigger={"soc": soc, **decision},
        )
        self.coordinator.update_state(free_event=event)
        await self._async_do_free_event_sell_start()

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
        event["soc_at_sell_start"] = self._essential_state(self.battery_soc_entity)
        event["meters_at_sell_start"] = self._read_daily_meters()
        self.coordinator.update_state(free_event=event)
        title = "🔋 Sunsynk: free event sell started" if api_ok else "⚠️ Sunsynk: free event sell NOT applied"
        await self.async_notify(
            title,
            f"Selling about {event['expected_export_kwh']} kWh down to {event['sell_floor_soc']}% "
            f"before the free period at {free_start.strftime('%H:%M')}.{_api_note(api_ok)}",
        )
        await self._async_report_event("selling", event)

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
        event["soc_at_free_start"] = self._essential_state(self.battery_soc_entity)
        event["meters_at_free_start"] = self._read_daily_meters()
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
        free_start = dt_util.as_local(dt_util.parse_datetime(event["free_start"]))
        free_end = dt_util.as_local(dt_util.parse_datetime(event["free_end"]))
        normal_pence = band_price_pence_per_kwh(
            self.cfg.get(CONF_CHARGES, []), free_start.strftime("%H:%M"), free_end.strftime("%H:%M"), "import"
        )
        event.update(free_event_result(
            event, self._read_daily_meters(), self._essential_state(self.battery_soc_entity), normal_pence,
            self._capacity_kwh(),
        ))
        event["phase"] = "done"
        self._clear_free_event_timers()
        self.coordinator.update_state(
            free_event=event, free_event_manual_start=None, free_event_manual_end=None
        )
        await self.data_logger.async_log_free_event({"phase": "done", **event})
        await self._async_report_event("done", event)
        await self.async_notify(
            "🔋 Sunsynk: free event finished",
            (
                f"Free period ended. Sold {_kwh(event['sold_kwh'])} (expected {_kwh(event.get('expected_export_kwh', 0.0))}), "
                f"imported {_kwh(event['free_import_kwh'])} free (expected refill {event['expected_refill_kwh']} kWh), "
                f"SOC now {_pct(event['soc_at_end'])}. Normal plan restored."
            ),
        )

    async def _async_restore_normal_plan(self) -> None:
        """Re-push the latest computed Flux 1 plan plus the standard Flux 2 slot (or an active export pause)."""
        plan = self.coordinator.state.last_import_plan
        now = dt_util.now()
        payload: dict[str, Any] = {
            "flux_2": restore_flux2_slot(
                bool(self.coordinator.state.evening_export_disabled), now.hour * 60 + now.minute
            )
        }
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
        await self._async_report_event("cancelled", event)
        await self.async_notify("🔋 Sunsynk: free event cancelled", "The free electricity event was cancelled.")

    def _slots_held_by(self) -> str | None:
        """Which event holds the Flux slots right now (do-not-break 7), if any.

        A free event holds them from its sell start (selling/charging), so the
        normal plan keeps running while it is only scheduled.
        """
        return "free_event" if self.coordinator.state.free_event.get("phase") in ("selling", "charging") else None

    def _slots_held(self) -> bool:
        return self._slots_held_by() is not None
