# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Optimizer mixin: weekly summary, AI insight, monthly reports, bill reconciliation, advisors."""
from __future__ import annotations

import calendar
import json
import os
from datetime import (
    date as date_cls,
    timedelta,
)
from typing import Any
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from .const import (
    DOMAIN,
    CONF_CHARGE_RATE,
    CONF_UPGRADE_PRICE_PER_KWH,
    CONF_PV_KWP,
    CONF_PV_UPGRADE_PRICE_PER_KWP,
    CONF_CHARGES,
    CONF_DATA_REPORT_TARGET,
    CONF_ENABLE_AI_WEEKLY_INSIGHT,
    CONF_COST_AWARE_EXPORT_SHADOW_MODE,
    CONF_PEAK_EXPORT_LIVE,
    CONF_GAS_UNIT_PENCE,
    CONF_GAS_STANDING_PENCE,
    CONF_HEAT_PUMP_PRICE_GBP,
    CONF_OCTOPUS_GAS_COST_SENSOR,
    CONF_BILL_START_DAY,
    DEFAULT_BILL_START_DAY,
    CONF_TARIFF_RATE_OVERRIDES,
    CONF_OCTOPUS_IMPORT_RATES_ENTITY,
    DEFAULT_CHARGE_RATE,
    DEFAULT_ENABLE_AI_WEEKLY_INSIGHT,
    DEFAULT_COST_AWARE_EXPORT_SHADOW_MODE,
    DEFAULT_PEAK_EXPORT_LIVE,
)
from .data_logger import DATA_DIR_NAME
from .octopus_public import async_fetch_agile_rates
from .planning import (
    api_week,
    monthly_report_due,
    TARIFF_IMPORT_BANDS,
    TARIFF_LABELS,
    TARIFF_RATES_AS_OF,
    bands_from_rates,
    compare_tariffs,
    flux_bands_from_charges,
    merge_tariff_rates,
    agile_prices_by_date,
    electrification_summary,
    ELECTRIFY_HISTORY_DAYS,
    export_limit_scenario,
    heat_pump_scenario,
    mean_band_price,
    price_at,
    agile_region_override,
    octopus_region,
    tariff_vat_percent,
    battery_value_summary,
    upgrade_advisor,
    upgrade_summary,
    UPGRADE_HISTORY_DAYS,
    logged_day_cost_gbp,
    reconcile_month,
    bill_period,
    WEEK_HISTORY_DAYS,
    SCORECARD_WINDOW_DAYS,
    scorecard_summary,
    shadow_scorecard,
    days_in_period,
    sum_field,
    trailing_week,
    week_kpis,
)

class ReportsMixin:
    """Weekly summary, AI insight, monthly reports, bill reconciliation, advisors (methods of SunsynkOptimizer)."""

    async def _async_monthly_reports(self) -> None:
        """Send last month's reports once, catching up a missed first Sunday (phase 57).

        State is written only after the sends, so a failure retries at the next
        18:00 or startup. Each report keeps its own error handling via _guarded.
        """
        today = dt_util.now().date()
        if not monthly_report_due(self.coordinator.state.last_monthly_report_month, today):
            return
        await self._guarded(lambda: self._async_send_monthly_tariff_comparison(force=True), "Monthly tariff comparison")
        await self._guarded(lambda: self._async_send_monthly_battery_value(force=True), "Monthly battery value")
        await self._guarded(self._async_send_upgrade_advisor, "Upgrade advisor")
        await self._guarded(self.async_run_capacity_estimate, "Battery capacity estimate")
        report_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        self.coordinator.update_state(last_monthly_report_month=report_month)

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
            "week_clipped_solar_kwh": _sum("clipped_solar_kwh"),
            "year_to_date": self.coordinator.state.last_year_to_date_cost or {},
            "cost_aware_export_shadow_tally": shadow_mode_tally,
            **week_kpis(paired_days),
            # Phase 17: shadow (or live) peak-export gain over the week.
            "week_peak_export_kwh": _sum("peak_export_kwh"),
            "week_peak_export_gain_gbp": _sum("peak_export_gain_gbp"),
            "peak_export_live": bool(self.cfg.get(CONF_PEAK_EXPORT_LIVE, DEFAULT_PEAK_EXPORT_LIVE)),
            "api_health_week": api_week(self.coordinator.state.api_health_days),  # phase 101
            # Phase 37: days behind week_battery_saving_gbp.
            "battery_saving_days": battery_value_summary(paired_days)["days"],
        }


        await self.async_notify_debug(
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

    def _agile_region(self, overrides: Any) -> str | None:
        """Agile region: the "agile_region" override, else the import rates entity's tariff code."""
        region = agile_region_override(overrides)
        if region:
            return region
        entity = self._cfg_str(CONF_OCTOPUS_IMPORT_RATES_ENTITY)
        state = self.hass.states.get(entity) if entity else None
        return octopus_region(state.attributes.get("tariff_code")) if state is not None else None

    async def _async_agile_prices(self, month: str, region: str | None) -> dict[str, list[float]]:
        """{date: 48 ex-VAT p/kWh} for a past month, cached in the data dir; {} when unavailable."""
        if region is None:
            return {}
        path = os.path.join(self.hass.config.path(DATA_DIR_NAME), f"agile_{region}_{month}.json")

        def _read() -> Any:
            try:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return None

        def _write(results: list[dict[str, Any]]) -> None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(results, fh)

        results = await self.hass.async_add_executor_job(_read)
        if not isinstance(results, list):
            first = dt_util.parse_date(f"{month}-01")
            start = dt_util.start_of_local_day(first)
            end = dt_util.start_of_local_day((first + timedelta(days=32)).replace(day=1))
            results = await async_fetch_agile_rates(async_get_clientsession(self.hass), region, start, end)
            if not results:
                return {}
            await self.hass.async_add_executor_job(_write, results)
        return agile_prices_by_date(results, dt_util.DEFAULT_TIME_ZONE)

    async def _async_send_monthly_battery_value(self, force: bool = False) -> None:
        """First Sunday of the month: what the battery + optimiser saved last month vs no battery (phase 37)."""
        today = dt_util.now().date()
        if today.day > 7 and not force:
            return
        month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        days = [
            d for d in await self.data_logger.async_load_paired_days(days=40)
            if str(d.get("date", "")).startswith(month)
        ]
        summary = battery_value_summary(days)
        if summary["saving_gbp"] is None:
            return  # No day had complete snapshots and prices.
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Monthly Battery Value",
                json.dumps({"type": "monthly_battery_value", "month": month, **summary}),
                target=data_report_target,
            )
        wear = " after battery wear" if self._wear_pence() is not None else ""
        await self.async_notify(
            "🔋 Sunsynk: battery value for " + month,
            f"Battery saved ~£{summary['saving_gbp']:.2f} this month{wear} ({summary['days']} days) "
            "vs the same days with no battery, 00:00–22:00 at your Flux rates.",
        )

    async def _async_send_upgrade_advisor(self) -> None:
        """Monthly: what +5/+10 kWh of battery or +2 kWp of PV would have saved (phase 50); report only."""
        cfg = self.cfg
        days = await self.data_logger.async_load_snapshot_days(UPGRADE_HISTORY_DAYS)
        free_days = {
            r.get("date") for r in await self.data_logger.async_load_records(UPGRADE_HISTORY_DAYS)
            if r.get("type") == "day_kpis" and (r.get("free_import_excluded_kwh") or 0) > 0
        }
        days = {d: snaps for d, snaps in days.items() if d not in free_days}

        def _opt(key: str) -> float | None:
            try:
                value = float(cfg.get(key))
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None

        prices, _sources = self._tariff_prices()
        report = upgrade_advisor(
            days, prices, flux_bands_from_charges(cfg.get(CONF_CHARGES, []), "export"), self._capacity_kwh(cfg),
            float(cfg.get(CONF_CHARGE_RATE, DEFAULT_CHARGE_RATE)), self._wear_pence(), _opt(CONF_PV_KWP),
            _opt(CONF_UPGRADE_PRICE_PER_KWH), _opt(CONF_PV_UPGRADE_PRICE_PER_KWP),
        )
        await self._add_electrification(report, cfg, prices, _opt)
        self.coordinator.update_state(touch=False, upgrade_advisor={**report, "date": dt_util.now().date().isoformat()})
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Upgrade Advisor", json.dumps({"type": "upgrade_advisor", **report}), target=data_report_target
            )
        lines = [t for t in (upgrade_summary(report), *electrification_summary(report)) if t]
        if lines:
            await self.async_notify("🔋 Sunsynk: what a bigger system would have saved", "\n".join(lines))

    async def _add_electrification(self, report: dict[str, Any], cfg: dict[str, Any], prices, opt) -> None:
        """Phase 78: heat pump on Cosy vs gas, and a higher inverter limit, over the 13-month log (report only)."""
        paired = await self.data_logger.async_load_paired_days(days=ELECTRIFY_HISTORY_DAYS)
        temps = await self._async_outside_temp_by_date(ELECTRIFY_HISTORY_DAYS)
        overrides = cfg.get(CONF_TARIFF_RATE_OVERRIDES)
        rates = merge_tariff_rates(overrides)
        cosy = mean_band_price(bands_from_rates(TARIFF_IMPORT_BANDS["cosy"], rates["cosy"]))
        hp_price = None if cosy is None else round(cosy * (1 + tariff_vat_percent(overrides) / 100), 2)
        report["heat_pump"] = heat_pump_scenario(
            paired, opt(CONF_GAS_UNIT_PENCE), hp_price, opt(CONF_GAS_STANDING_PENCE), opt(CONF_HEAT_PUMP_PRICE_GBP), temps,
        )
        export_p = price_at(flux_bands_from_charges(cfg.get(CONF_CHARGES, []), "export"), 12 * 60)
        report["export_limit"] = export_limit_scenario(paired, export_p)

    async def _async_send_shadow_scorecard(self) -> None:
        """First Sunday of the month: score every shadow feature against what happened (phase 41); read-only."""
        today = dt_util.now().date()
        if today.day > 7:
            return
        records = await self.data_logger.async_load_records(SCORECARD_WINDOW_DAYS + 2)
        card = shadow_scorecard(records, today.isoformat())
        self.coordinator.update_state(touch=False, shadow_scorecard=card)
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Shadow Scorecard",
                json.dumps({"type": "shadow_scorecard", "date": today.isoformat(), "features": card}),
                target=data_report_target,
            )
        await self.async_notify("🔋 Sunsynk: shadow feature scorecard", scorecard_summary(card))

    async def async_set_billed_amount(self, billed_gbp: float) -> None:
        """User entered the latest bill's net £ (number entity); reconcile it now (phase 36).

        The period is the latest complete one before today, from the configured bill start day."""
        start, end, label = bill_period(dt_util.now().date(), self.cfg.get(CONF_BILL_START_DAY, DEFAULT_BILL_START_DAY))
        self.coordinator.update_state(bill_reconciliation={
            "month": label, "start": start.isoformat(), "end": end.isoformat(), "billed_gbp": billed_gbp,
        })
        await self._guarded(self.async_run_bill_reconciliation, "Bill reconciliation")

    async def async_run_bill_reconciliation(self) -> dict[str, Any] | None:
        """Compare the month's logged Octopus costs with the billed £ the user entered (phase 36); read-only."""
        state = dict(self.coordinator.state.bill_reconciliation or {})
        month, billed = state.get("month"), state.get("billed_gbp")
        if not month or billed is None:
            return None
        if state.get("start") and state.get("end"):
            start, end = state["start"], state["end"]
        else:  # stored before bill periods: a calendar month "YYYY-MM"
            year, mon = (int(x) for x in month.split("-"))
            start, end = f"{month}-01", f"{month}-{calendar.monthrange(year, mon)[1]:02d}"
        days_in_month = (date_cls.fromisoformat(end) - date_cls.fromisoformat(start)).days + 1
        include_gas = bool(self._cfg_str(CONF_OCTOPUS_GAS_COST_SENSOR))
        days = {
            d["date"]: d for d in await self.data_logger.async_load_paired_days(days=100)
            if start <= str(d.get("date", "")) <= end
        }
        result = reconcile_month(
            [logged_day_cost_gbp(d, include_gas) for d in days.values()], days_in_month, float(billed)
        )
        result = {"month": month, "includes_gas": include_gas, **result}
        self.coordinator.update_state(bill_reconciliation={**state, "result": result})
        if result["coverage_low"] and result["days_logged"]:
            ir.async_create_issue(
                self.hass, DOMAIN, "bill_coverage_low", is_fixable=False,
                severity=ir.IssueSeverity.WARNING, translation_key="bill_coverage_low",
                translation_placeholders={
                    "logged": str(result["days_logged"]), "total": str(days_in_month), "period": month,
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, "bill_coverage_low")
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Bill Reconciliation",
                json.dumps({"type": "bill_reconciliation", **result}),
                target=data_report_target,
            )
        if result["logged_gbp"] is None:
            body = f"No logged Octopus cost for {month}, so the £{billed:.2f} bill can't be checked."
        else:
            body = (
                f"Billed £{result['billed_gbp']:.2f} vs logged £{result['logged_gbp']:.2f} "
                f"({result['days_logged']}/{days_in_month} days"
                + (", partial period" if result["partial"] else "")
                + f"): gap £{result['gap_gbp']:+.2f}."
            )
            if result["partial"] and result["projected_logged_gbp"] is not None:
                body += f" Projected to the full period: £{result['projected_logged_gbp']:.2f}"
                body += (
                    " (too few days logged to trust)." if result["coverage_low"]
                    else f", {result['gap_per_day_pct']:+.1f}% off the bill."
                )
            if result["alert"]:
                body += " Over £2: check credits, standing-charge or VAT changes, or a missed day's cost."
        title = "⚠️ Sunsynk: bill differs from logged cost" if result["alert"] else "🔋 Sunsynk: bill check for " + month
        await self.async_notify(title, body)
        return result

    async def _async_send_monthly_tariff_comparison(self, force: bool = False) -> None:
        """First Sunday of the month: cost last month's logged usage under Flux and the alternatives.

        Usage is held fixed (battery behaviour would differ on another tariff), so this is a
        guide, not a quote. Totals include standing charges and VAT (phase 35).
        """
        today = dt_util.now().date()
        if today.day > 7 and not force:
            return
        month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        imp, exp = await self.data_logger.async_load_half_hourly()
        overrides = self.cfg.get(CONF_TARIFF_RATE_OVERRIDES)
        rates = merge_tariff_rates(overrides)
        def export_flat(r: dict[str, float]) -> list[tuple[int, int, float]]:
            return [(0, 1440, r["export"])]

        tariffs = {
            "flux": (
                flux_bands_from_charges(self.cfg.get(CONF_CHARGES), "import"),
                flux_bands_from_charges(self.cfg.get(CONF_CHARGES), "export"),
            ),
            **{
                name: (bands_from_rates(TARIFF_IMPORT_BANDS[name], rates[name]), export_flat(rates[name]))
                for name in ("go", "cosy", "intelligent")
            },
        }
        if any(d.startswith(month) for d in imp):
            tariffs["agile"] = (
                await self._async_agile_prices(month, self._agile_region(overrides)),
                export_flat(rates["agile"]),
            )
        result = compare_tariffs(
            imp, exp, tariffs, month,
            standing={name: r.get("standing") for name, r in rates.items()},
            vat_percent=tariff_vat_percent(overrides),
        )
        if not result["days_compared"]:
            return  # Consumption sensors not configured, or no complete days logged.
        data_report_target = self._cfg_str(CONF_DATA_REPORT_TARGET)
        if data_report_target:
            await self.async_notify_debug(
                "Sunsynk Monthly Tariff Comparison",
                json.dumps({"type": "monthly_tariff_comparison", **result}),
                target=data_report_target,
            )
        lines = [
            f"{TARIFF_LABELS[k]}: "
            + (f"£{v:.2f}" if v is not None else "n/a")
            + (f" (units £{result['net_gbp'][k]:.2f})" if v is not None and result["net_gbp"][k] is not None else "")
            for k, v in result["total_gbp"].items()
        ]
        stale = (
            " Flux prices here are your configured tariff rows, which differ from Octopus's current rates."
            if self.coordinator.state.tariff_price_alert.get("changes")
            else ""
        )
        await self.async_notify(
            "🔋 Sunsynk: tariff comparison for " + month,
            f"{result['days_compared']} days, same usage, inc. standing charge and {result['vat_percent']:g}% VAT, "
            "net of export. " + " · ".join(lines)
            + f". Rates as of {TARIFF_RATES_AS_OF}; battery use would differ on another tariff.{stale}",
        )
