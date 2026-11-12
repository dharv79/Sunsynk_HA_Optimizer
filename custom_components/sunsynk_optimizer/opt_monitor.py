# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: drift check, data audit, tariff check, PV strings, inverter events, battery health."""
from __future__ import annotations

import json
import logging
from typing import Any
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util
from .const import (
    DOMAIN,
    CONF_CHARGES,
    CONF_BATTERY_COST_GBP,
    CONF_BATTERY_LIFETIME_KWH,
    CONF_OCTOPUS_IMPORT_COST_SENSOR,
    GENTLE_CHARGE_SETTING,
)
from .flux_helpers import kpi_prices_pence
from .planning import (
    battery_health_trend,
    bms_summary_line,
    health_fault_flag,
    inverter_fault_flag,
    ledger_point,
    parse_battery_health,
    new_grid_notices,
    new_inverter_events,
    normalise_grid_notices,
    normalise_inverter_counts,
    normalise_inverter_events,
    STRING_ALERT_STREAK,
    STRING_MIN_TOTAL_KW,
    STRING_SAMPLE_FROM_HOUR,
    STRING_SAMPLE_TO_HOUR,
    string_ratio_anomaly,
    string_share,
    string_streak,
    AUDIT_DAYS,
    audit_days,
    audit_summary,
    CLIPPED_SOC_PCT,
    DRIFT_CHECK_AFTER_PUSH_MINUTES,
    expected_grid_current_a,
    flux_drift,
    grid_current_drift,
    tariff_price_changes,
)
from .opt_common import (
    TARIFF_BAND_LABELS,
    _drift_line,
)

_LOGGER = logging.getLogger(__name__)

class MonitorMixin:
    """Drift check, data audit, tariff check, PV strings, inverter events, battery health (methods of SunsynkOptimizer)."""

    async def _async_track_pv_strings(self) -> None:
        """Tally each MPPT string's energy over bright mid-day hours (phase 49); no writes.

        Skipped when the battery is full (clipping curtails strings unevenly), below
        1 kW total, outside 10:00-15:00, or when either string is unavailable
        (a single-MPPT system is a no-op).
        """
        now = dt_util.now()
        if not STRING_SAMPLE_FROM_HOUR <= now.hour < STRING_SAMPLE_TO_HOUR:
            return
        today = now.date().isoformat()
        if self._strings.get("date") != today:
            self._strings = {"date": today, "m0": 0.0, "m1": 0.0}
        p0, p1 = (self._essential_state(e) for e in (self.pv_mppt0_entity, self.pv_mppt1_entity))
        soc = self._essential_state(self.battery_soc_entity)
        if p0 is None or p1 is None or (soc is not None and soc >= CLIPPED_SOC_PCT):
            return
        if (p0 + p1) / 1000 < STRING_MIN_TOTAL_KW:
            return
        self._strings["m0"] += p0 / 1000 * 0.5
        self._strings["m1"] += p1 / 1000 * 0.5

    async def _async_capture_pv_strings(self, date: str) -> None:
        """22:00: log today's string energies, judge the split against the prior days, alert on a 3-day streak."""
        sample = self._strings if self._strings.get("date") == date else {}
        m0, m1 = sample.get("m0"), sample.get("m1")
        share = string_share(m0, m1)
        if share is None:
            return
        records = await self.data_logger.async_load_records(24)
        history = [
            r["share"] for r in records
            if r.get("type") == "pv_strings" and r.get("date") != date and isinstance(r.get("share"), (int, float))
        ]
        result = string_ratio_anomaly(history, share)
        await self.data_logger.async_log_pv_strings(
            date=date, mppt0_kwh=round(m0, 2), mppt1_kwh=round(m1, 2), share=share,
            median=None if result is None else result["median"], anomalous=None if result is None else result["anomalous"],
        )
        prev = self.coordinator.state.pv_strings or {}
        streak, weaker = string_streak(int(prev.get("streak") or 0), prev.get("weaker"), result)
        self.coordinator.update_state(
            touch=False,
            pv_strings={"date": date, "share": share, "median": None if result is None else result["median"],
                        "streak": streak, "weaker": weaker, "mppt0_kwh": round(m0, 2), "mppt1_kwh": round(m1, 2)},
        )
        if result is not None and streak == STRING_ALERT_STREAK and weaker:
            await self.async_notify(
                "🔋 Sunsynk: PV string output low",
                f"{weaker.upper()} has produced well below its usual share for {streak} days "
                f"(MPPT0 share {share * 100:.0f}% vs usual {result['median'] * 100:.0f}%). "
                "Check for shading, soiling or a failed connector.",
            )

    async def _async_data_audit(self, _now) -> None:
        await self._guarded(self.async_run_data_audit, "Data audit")

    async def async_run_data_audit(self) -> None:
        """06:20: audit the last 14 days of records (phase 45); raise or clear HA Repairs issues.

        Read-only apart from the fill-only Octopus cost backfill (rule 12) when a settled
        daily_cost is missing. Issues are one Repairs entry per class (missing / duplicate /
        contradiction), deleted as soon as the class clears. Never rewrites a record.
        """
        today = dt_util.now().date()
        expect_cost = bool(self._cfg_str(CONF_OCTOPUS_IMPORT_COST_SENSOR))
        records = await self.data_logger.async_load_records(AUDIT_DAYS + 3)
        issues = audit_days(records, today, AUDIT_DAYS, expect_cost)
        if expect_cost and any(i["type"] == "daily_cost" for i in issues):
            await self._async_capture_daily_cost()
            records = await self.data_logger.async_load_records(AUDIT_DAYS + 3)
            issues = audit_days(records, today, AUDIT_DAYS, expect_cost)
        summary = audit_summary(issues)
        self.coordinator.update_state(touch=False, data_health=summary)
        for kind in ("missing", "duplicate", "contradiction"):
            issue_id = f"data_{kind}"
            dates = summary["classes"].get(kind)
            if dates:
                ir.async_create_issue(
                    self.hass, DOMAIN, issue_id, is_fixable=False,
                    severity=ir.IssueSeverity.WARNING, translation_key=issue_id,
                    translation_placeholders={"count": str(len(dates)), "items": ", ".join(dates[:6])},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    async def _async_tariff_price_check(self, _now) -> None:
        await self._guarded(self.async_run_tariff_price_check, "Tariff price check")

    async def async_run_tariff_price_check(self) -> None:
        """06:10: alert once when the Octopus rates no longer match the configured `charges` (phase 33)."""
        octopus = self._octopus_band_prices(dt_util.now().date())
        if not octopus:
            return  # No Octopus rate entities configured.
        changes = tariff_price_changes(kpi_prices_pence(self.cfg.get(CONF_CHARGES, [])), octopus)
        previous = self.coordinator.state.tariff_price_alert.get("signature")
        if not changes:
            if previous:
                self.coordinator.update_state(tariff_price_alert={})
            return
        signature = json.dumps(changes)
        if signature == previous:
            return
        self.coordinator.update_state(
            tariff_price_alert={"signature": signature, "since": dt_util.now().date().isoformat(), "changes": changes}
        )
        await self.async_notify(
            "🔋 Sunsynk: Flux prices changed",
            "; ".join(f"{TARIFF_BAND_LABELS.get(b, b)} {mine:.2f}p → {theirs:.2f}p" for b, mine, theirs in changes)
            + ". Decisions already use the Octopus rates; update Configure → tariff rows so the "
            "Sunsynk app's income figures and the fallback match.",
        )

    def _schedule_drift_check(self) -> None:
        """(Re)arm a drift check DRIFT_CHECK_AFTER_PUSH_MINUTES after the latest write."""
        if self._drift_check_unsub:
            self._drift_check_unsub()
        self._drift_check_unsub = async_call_later(
            self.hass, DRIFT_CHECK_AFTER_PUSH_MINUTES * 60, self._async_drift_check
        )

    async def _async_drift_check(self, _now=None) -> None:
        self._drift_check_unsub = None
        await self._guarded(self.async_run_drift_check, "Inverter drift check")

    async def _async_read_flux_drift(self) -> list[dict[str, Any]]:
        expected = self.coordinator.state.last_pushed_flux
        if not expected or self._flux_read_supported is False:
            return []
        try:
            actual = await self.coordinator.api.async_read_flux_products(self.plant_id)
        except Exception as exc:  # pragma: no cover - network
            _LOGGER.debug("Flux read-back failed: %s", exc)
            return []
        if actual is None:
            _LOGGER.warning("Plant income read returned no fluxProducts; Flux drift check off until restart")
            self._flux_read_supported = False
            return []
        self._flux_read_supported = True
        return flux_drift(expected, actual)

    async def _async_read_current_drift(self, now) -> list[dict[str, Any]]:
        expected = expected_grid_current_a(self.coordinator.state.gentle_charge, now.date().isoformat())
        if expected is None:
            return []
        try:
            settings = await self.coordinator.api.async_read_settings(self.inverter_serial)
        except Exception as exc:  # pragma: no cover - network
            _LOGGER.debug("Settings read-back failed: %s", exc)
            return []
        return grid_current_drift(expected, settings.get(GENTLE_CHARGE_SETTING))

    async def _async_api_read(self, label: str, reader, *args):
        """One read-only cloud call; None (logged at debug) on any failure."""
        try:
            return await reader(*args)
        except Exception as exc:  # pragma: no cover - network
            _LOGGER.debug("%s read failed: %s", label, exc)
            return None

    async def async_check_inverter_events(self) -> None:
        """Read the inverter's fault signals and the cloud grid notices (phase 43); read-only, so also in monitor mode.

        Realtime events and account fault counts drive binary_sensor.inverter_fault; an unreadable source
        keeps its last value (never 'no faults'). New events are logged once and quoted in one notification.
        New cloud grid notices are logged as grid_notice records alongside the voltage detector's state."""
        api = self.coordinator.api
        previous = self.coordinator.state.inverter_events or {}
        events = normalise_inverter_events(
            await self._async_api_read("Inverter events", api.async_read_inverter_events, self.inverter_serial)
        )
        counts = normalise_inverter_counts(await self._async_api_read("Inverter counts", api.async_read_inverter_counts))
        health = await self.async_refresh_battery_health()
        fresh = new_inverter_events(events, previous.get("seen") or []) if events is not None else []
        active = (
            [{k: e[k] for k in ("text", "code", "time", "category")} for e in events]
            if events is not None else previous.get("active")
        )
        counts = counts if counts is not None else previous.get("counts")
        if events is not None or counts is not None or health is not None:
            self.coordinator.update_state(
                touch=False,
                inverter_events={
                    "active": active,
                    "counts": counts,
                    "fault": inverter_fault_flag(active, counts, health_fault_flag(health)),
                    "seen": ((previous.get("seen") or []) + [e["key"] for e in fresh])[-100:],
                    "checked": dt_util.now().isoformat(),
                },
            )
        for event in fresh:
            await self.data_logger.async_log_inverter_event(
                text=event["text"], code=event["code"], event_time=event["time"],
                category=event["category"], raw=event["raw"],
            )
        if fresh:
            lines = [f"• {e['text'] or e['code'] or 'unknown event'} ({e['category']})" for e in fresh[:5]]
            await self.async_notify("🔋 Sunsynk: inverter event", "Inverter reported:\n" + "\n".join(lines))
        await self._async_log_cloud_grid_notices()

    async def async_refresh_battery_health(self) -> dict[str, Any] | None:
        """Read the newest BMS register snapshot (phase 75); read-only, so also in monitor mode.

        Updates state.battery_health (latest snapshot, a compact per-day ledger and the trend) and logs one
        battery_health record per day. Returns the parsed snapshot, or None when unreadable (state kept)."""
        now = dt_util.now()
        today = now.date().isoformat()
        health = parse_battery_health(
            await self._async_api_read(
                "Battery health", self.coordinator.api.async_read_latest_workdata, self.inverter_serial, today
            )
        )
        if health is None:
            return None
        previous = self.coordinator.state.battery_health or {}
        ledger = list(previous.get("ledger") or [])
        point = ledger_point(today, health)
        new_day = point is not None and (not ledger or ledger[-1].get("date") != today)
        if point is not None:
            ledger = (ledger if new_day else ledger[:-1]) + [point]
        ledger = ledger[-800:]
        cfg = self.cfg
        trend = battery_health_trend(ledger, cfg.get(CONF_BATTERY_COST_GBP), cfg.get(CONF_BATTERY_LIFETIME_KWH))
        self.coordinator.update_state(
            touch=False,
            battery_health={"latest": health, "ledger": ledger, "trend": trend, "checked": now.isoformat()},
        )
        if new_day:
            await self.data_logger.async_log_battery_health(
                date=today, **{k: v for k, v in health.items() if k != "fault_words"},
                fault_words=health.get("fault_words") or {}, trend=trend,
            )
        return health

    async def _async_bms_line(self) -> str | None:
        """BMS context for a charge alert: the snapshot if read in the last 15 min, else a fresh read
        (falling back to the last snapshot when that read fails)."""
        stored = self.coordinator.state.battery_health or {}
        checked = dt_util.parse_datetime(stored.get("checked") or "")
        fresh = checked is not None and (dt_util.now() - checked).total_seconds() < 900
        health = stored.get("latest") if fresh else (await self.async_refresh_battery_health() or stored.get("latest"))
        return bms_summary_line(health)

    async def _async_log_cloud_grid_notices(self) -> None:
        notices = normalise_grid_notices(
            await self._async_api_read("Cloud notices", self.coordinator.api.async_read_notices), self.inverter_serial
        )
        if notices is None:
            return
        seen = (self.coordinator.state.cloud_grid_notices or {}).get("seen") or []
        fresh = new_grid_notices(notices, seen)
        if not fresh:
            return
        detector_active = self._outage_active()
        for notice in fresh:
            await self.data_logger.async_log_grid_notice(
                notice_id=notice["id"], kind=notice["kind"], notice_time=notice["time"],
                detector_outage_active=detector_active,
            )
        self.coordinator.update_state(
            touch=False,
            cloud_grid_notices={"seen": (seen + [n["id"] for n in fresh])[-100:], "last": fresh[-1]},
        )

    async def async_run_drift_check(self) -> None:
        """Read back what the inverter holds and alert once per drift episode.

        Read-only: never re-pushes. Skipped in monitor mode (nothing pushed is
        authoritative there).
        """
        await self.async_check_inverter_events()
        if self.operation_mode == "monitor":
            return
        now = dt_util.now()
        diffs = await self._async_read_flux_drift() + await self._async_read_current_drift(now)
        previous = self.coordinator.state.inverter_drift.get("signature")
        if not diffs:
            if previous:
                self.coordinator.update_state(inverter_drift={})
            return
        signature = json.dumps(diffs, sort_keys=True)
        if signature == previous:
            return
        self.coordinator.update_state(
            inverter_drift={"signature": signature, "since": now.isoformat(), "diffs": diffs}
        )
        await self.data_logger.async_log_inverter_drift(date=now.date().isoformat(), time=now.strftime("%H:%M"), diffs=diffs)
        await self.async_notify(
            "⚠️ Sunsynk: inverter settings differ from plan",
            "; ".join(_drift_line(d) for d in diffs) + ". Not re-pushed; check the Sunsynk app.",
        )
