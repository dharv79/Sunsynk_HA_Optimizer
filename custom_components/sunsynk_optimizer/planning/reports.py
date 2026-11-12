# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Tariff comparison, bill reconciliation, value report, upgrade and electrification advisors."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from .core import (
    BATTERY_LOW_SOC,
    CHARGE_EFFICIENCY,
    DISCHARGE_EFFICIENCY,
    HALF_HOURS_PER_DAY,
    _BASELINE_SEGMENTS,
    _as_aware,
    _is_number,
    _pence,
    _seg_value,
    _snap,
    hhmm_to_minutes,
    net_cost_gbp,
    price_at,
    round_or_none,
)


_COMPLETE_COST_FIELDS = ("actual_import_cost_gbp", "actual_export_income_gbp", "actual_gas_cost_gbp")


def latest_complete_cost_day(days: list[dict[str, Any]], since: str) -> dict[str, Any] | None:
    """Newest day on/after ISO `since` with import, export and gas all logged.

    Octopus settles each sensor on its own lag (import/gas often a day behind
    export), so the newest logged day is usually partial. Returns a
    `daily_cost_complete` record with its net cost, or None if no day in the
    window is complete. Gas is required, matching net_cost_gbp.
    """
    complete = [
        d for d in days
        if str(d.get("date", "")) >= since and all(d.get(f) is not None for f in _COMPLETE_COST_FIELDS)
    ]
    if not complete:
        return None
    day = max(complete, key=lambda d: str(d["date"]))
    return {
        "type": "daily_cost_complete",
        "date": day["date"],
        **{f: day[f] for f in _COMPLETE_COST_FIELDS},
        "net_cost_gbp": net_cost_gbp(*(day[f] for f in _COMPLETE_COST_FIELDS)),
    }


# --- Tariff comparison (phase 28) -------------------------------------------

# Approximate public unit rates in pence/kWh, last checked 13/10/2026. They drift
# with Ofgem's cap, so users can override any rate via the options JSON.
TARIFF_RATES_AS_OF = "13/10/2026"

# "standing" is p/day: Octopus charges the same regional standing charge on its
# variable tariffs, defaulted from the user's Flux bills (54.62p, 14/10/2026).
# Unit rates are ex-VAT; VAT is applied once in compare_tariffs.
TARIFF_STANDING_P_PER_DAY = 54.62

TARIFF_DEFAULT_RATES: dict[str, dict[str, float]] = {
    "flux": {"standing": TARIFF_STANDING_P_PER_DAY},  # unit rates come from the user's charges
    "go": {"cheap": 8.5, "day": 28.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "cosy": {"cheap": 13.5, "peak": 40.5, "day": 27.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "intelligent": {"cheap": 7.0, "day": 28.0, "export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},
    "agile": {"export": 12.0, "standing": TARIFF_STANDING_P_PER_DAY},  # import priced per slot
}

# Domestic VAT on the user's bills is 0% from 01/10/2026; override with "vat_percent".
TARIFF_VAT_PERCENT = 0.0


def band_price(bands: list[tuple[int, int, float]], minute: int) -> float | None:
    """Price (p/kWh) of the band covering ``minute`` of the day; None when no band does."""
    for start, end, price in bands:
        inside = start <= minute < end if start < end else (minute >= start or minute < end)
        if inside:
            return price
    return None


def bands_from_rates(
    spec: list[tuple[str, str, str]], rates: dict[str, float]
) -> list[tuple[int, int, float]]:
    """Resolve a (start, end, rate key) spec into minute bands; bands with a bad key or time are dropped."""
    out: list[tuple[int, int, float]] = []
    for start, end, key in spec:
        s, e, price = hhmm_to_minutes(start), hhmm_to_minutes(end), rates.get(key)
        if s is not None and e is not None and isinstance(price, (int, float)):
            out.append((s, e, float(price)))
    return out


def flux_bands_from_charges(charges: Any, status: str) -> list[tuple[int, int, float]]:
    """The user's configured Flux bands for ``status`` ("import"/"export") as minute bands."""
    out: list[tuple[int, int, float]] = []
    for row in charges if isinstance(charges, list) else []:
        if not isinstance(row, dict) or row.get("status") != status:
            continue
        s, e = hhmm_to_minutes(row.get("startRange")), hhmm_to_minutes(row.get("endRange"))
        price = row.get("price")
        if s is not None and e is not None and isinstance(price, (int, float)):
            out.append((s, e, float(price)))
    return out


def _overrides_dict(overrides: Any) -> dict[str, Any]:
    """The options' tariff overrides JSON as a dict; junk → {}."""
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides) if overrides.strip() else {}
        except ValueError:
            overrides = {}
    return overrides if isinstance(overrides, dict) else {}


def tariff_vat_percent(overrides: Any) -> float:
    """VAT % for the comparison: the "vat_percent" override when valid (0-100), else the dated default."""
    vat = _overrides_dict(overrides).get("vat_percent")
    return float(vat) if _is_number(vat) and 0 <= vat <= 100 else TARIFF_VAT_PERCENT


def agile_region_override(overrides: Any) -> str | None:
    """The "agile_region" override (a DNO region letter A-P), else None."""
    region = _overrides_dict(overrides).get("agile_region")
    return region.upper() if isinstance(region, str) and re.fullmatch(r"[A-Pa-p]", region) else None


def octopus_region(tariff_code: Any) -> str | None:
    """Region letter from an Octopus tariff code ("E-1R-FLUX-IMPORT-23-02-14-C" → "C")."""
    match = re.fullmatch(r"E-[12]R-.+-([A-P])", str(tariff_code or ""))
    return match.group(1) if match else None


def agile_prices_by_date(results: Any, tz: Any = None) -> dict[str, list[float]]:
    """{local date: 48 ex-VAT p/kWh} from Octopus standard-unit-rates results.

    Only complete days are kept (DST days with 46/50 slots and partial days
    are dropped rather than guessed, matching parse_half_hourly_charges).
    """
    slots: dict[str, dict[int, float]] = {}
    for row in results if isinstance(results, list) else []:
        if not isinstance(row, dict) or not _is_number(row.get("value_exc_vat")):
            continue
        start = _as_aware(row.get("valid_from"), tz)
        if start is None:
            continue
        local = start.astimezone(tz) if tz is not None else start
        slots.setdefault(local.date().isoformat(), {})[local.hour * 2 + local.minute // 30] = float(
            row["value_exc_vat"]
        )
    return {
        day: [by_idx[i] for i in range(HALF_HOURS_PER_DAY)]
        for day, by_idx in slots.items()
        if len(by_idx) == HALF_HOURS_PER_DAY
    }


def newest_agile_product(products: list[dict[str, Any]], before: datetime) -> str | None:
    """Code of the newest Agile import product that was on sale by ``before``."""
    best: tuple[str, str] | None = None
    for product in products:
        code, available = str(product.get("code", "")), str(product.get("available_from") or "")
        if not code.startswith("AGILE-") or "OUTGOING" in code or product.get("direction") not in (None, "IMPORT"):
            continue
        if available and available[:10] > before.date().isoformat():
            continue
        if best is None or available > best[1]:
            best = (code, available)
    return best[0] if best else None


def merge_tariff_rates(overrides: Any) -> dict[str, dict[str, float]]:
    """Default rates with user overrides applied; unknown tariffs/keys and non-numbers are ignored."""
    rates = {name: dict(r) for name, r in TARIFF_DEFAULT_RATES.items()}
    for name, vals in _overrides_dict(overrides).items():
        if name in rates and isinstance(vals, dict):
            for key, val in vals.items():
                if key in rates[name] and isinstance(val, (int, float)) and not isinstance(val, bool):
                    rates[name][key] = float(val)
    return rates


def parse_half_hourly_charges(attrs: Any) -> tuple[str, list[float]] | None:
    """(date, 48 kWh values by half-hour of the local day) from an Octopus ``charges`` attribute.

    None unless the day is complete: exactly 48 distinct slots on one date, all numeric
    (DST days with 46/50 slots are skipped rather than guessed — rule 11).
    """
    charges = attrs.get("charges") if isinstance(attrs, dict) else None
    if not isinstance(charges, list) or len(charges) != HALF_HOURS_PER_DAY:
        return None
    slots: list[float | None] = [None] * HALF_HOURS_PER_DAY
    day: str | None = None
    for row in charges:
        try:
            start = datetime.fromisoformat(str(row["start"]))
            kwh = row["consumption"]
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(kwh, (int, float)) or isinstance(kwh, bool):
            return None
        if day is None:
            day = start.date().isoformat()
        elif start.date().isoformat() != day:
            return None
        idx = start.hour * 2 + start.minute // 30
        if slots[idx] is not None:
            return None
        slots[idx] = float(kwh)
    if day is None or any(v is None for v in slots):
        return None
    return day, [v for v in slots if v is not None]


def cost_under_tariff(half_hourly_kwh: Any, bands: list[tuple[int, int, float]] | list[float]) -> float | None:
    """Pence for a day of 48 half-hourly kWh; None if any slot is missing or unpriced.

    ``bands`` is either minute bands (start, end, p) or 48 per-slot prices (Agile).
    """
    if not isinstance(half_hourly_kwh, (list, tuple)) or len(half_hourly_kwh) != HALF_HOURS_PER_DAY:
        return None
    per_slot = bool(bands) and all(_is_number(b) for b in bands)
    if per_slot and len(bands) != HALF_HOURS_PER_DAY:
        return None
    total = 0.0
    for idx, kwh in enumerate(half_hourly_kwh):
        price = float(bands[idx]) if per_slot else band_price(bands, idx * 30)
        if price is None or not isinstance(kwh, (int, float)) or isinstance(kwh, bool):
            return None
        total += kwh * price
    return round(total, 2)


def compare_tariffs(
    import_by_date: dict[str, list[float]],
    export_by_date: dict[str, list[float]],
    tariffs: dict[str, tuple[Any, Any]],
    month: str,
    standing: dict[str, float] | None = None,
    vat_percent: float = 0.0,
) -> dict[str, Any]:
    """Per-tariff £ over the days of ``month`` ("YYYY-MM"), usage held fixed.

    Each tariff is (import, export) pricing: minute bands, or for Agile a
    {date: 48 slot prices} dict. ``net_gbp`` is unit rates only (import cost
    minus export income, kept for continuity); ``total_gbp`` adds the standing
    charge (p/day) and VAT on import + standing — closer to a bill. Only days
    with both import and export logged count; a tariff that can't price some
    counted day (or has no standing charge) gets None, never a partial total.
    """
    days = sorted(d for d in import_by_date if d.startswith(month) and d in export_by_date)
    standing = standing or {}
    net: dict[str, float | None] = {}
    total: dict[str, float | None] = {}
    for name, (imp_spec, exp_spec) in tariffs.items():
        imp_p: float | None = 0.0 if days else None
        exp_p = 0.0
        for d in days:
            imp_prices = imp_spec.get(d) if isinstance(imp_spec, dict) else imp_spec
            exp_prices = exp_spec.get(d) if isinstance(exp_spec, dict) else exp_spec
            imp = cost_under_tariff(import_by_date[d], imp_prices) if imp_prices else None
            exp = cost_under_tariff(export_by_date[d], exp_prices) if exp_prices else None
            if imp is None or exp is None:
                imp_p = None
                break
            imp_p += imp
            exp_p += exp
        net[name] = None if imp_p is None else round((imp_p - exp_p) / 100.0, 2)
        day_charge = standing.get(name)
        total[name] = (
            None
            if imp_p is None or not _is_number(day_charge)
            else round(((imp_p + day_charge * len(days)) * (1 + vat_percent / 100.0) - exp_p) / 100.0, 2)
        )
    ranked = total if any(v is not None for v in total.values()) else net
    priced = {k: v for k, v in ranked.items() if v is not None}
    cheapest = min(priced, key=lambda k: priced[k]) if priced else None
    return {
        "month": month,
        "days_compared": len(days),
        "days_missing": sum(1 for d in import_by_date if d.startswith(month) and d not in export_by_date)
        + sum(1 for d in export_by_date if d.startswith(month) and d not in import_by_date),
        "net_gbp": net,
        "total_gbp": total,
        "standing_p_per_day": {k: standing.get(k) for k in tariffs},
        "vat_percent": vat_percent,
        "cheapest": cheapest,
        "rates_as_of": TARIFF_RATES_AS_OF,
    }


def meter_delta(start: dict[str, Any] | None, end: dict[str, Any] | None, key: str) -> float | None:
    """kWh between two daily-meter readings; None when either is missing or the meter reset (midnight)."""
    a, b = (start or {}).get(key), (end or {}).get(key)
    if not _is_number(a) or not _is_number(b) or b < a:
        return None
    return round(b - a, 2)


# --------------------------------------------------------------------------- #
# Tariff price-change alert (phase 33)                                         #
# --------------------------------------------------------------------------- #

TARIFF_PRICE_TOLERANCE_PENCE = 0.05


def tariff_price_changes(
    configured: dict[str, float | None],
    octopus: dict[str, float | None],
    tolerance: float = TARIFF_PRICE_TOLERANCE_PENCE,
) -> list[tuple[str, float, float]]:
    """(band, configured p, Octopus p) for each band whose prices disagree.

    A band missing or None on either side is ignored, so an unavailable
    Octopus entity never reads as a change to 0p.
    """
    changes = []
    for band, mine in configured.items():
        theirs = octopus.get(band)
        if mine is None or theirs is None:
            continue
        if abs(theirs - mine) > tolerance:
            changes.append((band, round(mine, 2), round(theirs, 2)))
    return changes


# Battery and PV upgrade advisor (phase 50). Report only: never changes a setting.
UPGRADE_BATTERY_STEPS_KWH = (5.0, 10.0)

UPGRADE_PV_STEP_KWP = 2.0

UPGRADE_MIN_DAYS = 7


def _day_flows(
    snapshots: dict[str, dict[str, Any]], battery_capacity_kwh: float
) -> list[tuple[str, str, float, float]] | None:
    """[(segment start, band, house load, solar)] per `_BASELINE_SEGMENTS`, as phase 37 derives them; None if incomplete."""
    flows = []
    for start, end, band, sunny in _BASELINE_SEGMENTS:
        load = _seg_value(snapshots, start, end, "load_kwh")
        imp = _seg_value(snapshots, start, end, "grid_import_kwh")
        exp = _seg_value(snapshots, start, end, "grid_export_kwh")
        if load is None or imp is None or exp is None:
            return None
        solar = 0.0
        if sunny:
            soc_a, soc_b = _snap(snapshots, start, "soc"), _snap(snapshots, end, "soc")
            if soc_a is None or soc_b is None:
                return None
            solar = max(0.0, load - imp + exp + (soc_b - soc_a) / 100 * battery_capacity_kwh)
        flows.append((start, band, load, solar))
    return flows


def simulate_system(
    snapshots: dict[str, dict[str, Any]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    sim_capacity_kwh: float,
    charge_kw: float,
    pv_scale: float = 1.0,
    wear_pence: float | None = None,
    floor_soc: float = BATTERY_LOW_SOC,
) -> dict[str, float | None]:
    """00:00-22:00 cost of one logged day re-run with a `sim_capacity_kwh` battery and PV × `pv_scale`.

    Load and solar per segment come from the meters as in phase 37
    (`battery_capacity_kwh` is the real battery, used only for that). The
    simulated battery starts the day at its floor, charges at 02:00-05:00
    (≤ charge_kw × 3 h) just enough for the later deficits it can serve more
    cheaply than the grid (perfect foresight: an upper bound for every size,
    so compare sizes, don't read the absolute), soaks up solar surplus and
    discharges into any deficit. Energy left at 22:00 is credited at the
    off-peak price it saves tomorrow. With a 0 kWh battery the cost equals
    phase 37's `no_battery_gbp`. Missing data → all None.
    """
    empty = {"cost_gbp": None, "import_kwh": None, "export_kwh": None, "cycled_kwh": None}
    flows = _day_flows(snapshots, battery_capacity_kwh)
    offpeak = prices_pence.get("offpeak")
    if flows is None or battery_capacity_kwh <= 0:
        return empty
    ce, de, wear = CHARGE_EFFICIENCY, DISCHARGE_EFFICIENCY, wear_pence or 0.0
    usable = max(0.0, sim_capacity_kwh) * (1 - floor_soc / 100)
    flows = [(s, b, load, solar * pv_scale) for s, b, load, solar in flows]

    def worth_it(band: str) -> bool:
        price = prices_pence.get(band)
        return offpeak is not None and price is not None and price > (offpeak + wear) / (ce * de)

    # Backward pass from 22:00: stored kWh needed at 05:00 to cover later deficits.
    need = 0.0
    for _start, band, load, solar in reversed(flows[2:]):
        deficit = max(0.0, load - solar) if worth_it(band) else 0.0
        need = max(0.0, need + deficit / de - max(0.0, solar - load) * ce)
    stored = cost = imported = exported = cycled = 0.0
    for start, band, load, solar in flows:
        imp_price, exp_price = prices_pence.get(band), price_at(export_bands, hhmm_to_minutes(start))
        grid_in = grid_out = 0.0
        if start == "02:00" and usable > 0:
            add = max(0.0, min(need, usable, charge_kw * 3 * ce) - stored)
            stored += add
            cycled += add / ce
            grid_in += add / ce
        surplus = solar - load
        if surplus >= 0:
            take = min(usable - stored, surplus * ce)
            stored += take
            cycled += take / ce
            grid_out += surplus - take / ce
        else:
            draw = min(stored, -surplus / de) if start != "02:00" else 0.0
            stored -= draw
            grid_in += -surplus - draw * de
        parts = (_pence(grid_in, imp_price), _pence(grid_out, exp_price))
        if any(p is None for p in parts):
            return empty
        cost += parts[0] - parts[1]
        imported += grid_in
        exported += grid_out
    if stored > 0:
        if offpeak is None:
            return empty
        cost -= stored / ce * offpeak
    cost += cycled * wear
    return {
        "cost_gbp": round(cost / 100, 2),
        "import_kwh": round(imported, 2),
        "export_kwh": round(exported, 2),
        "cycled_kwh": round(cycled, 2),
    }


def upgrade_scenarios(
    battery_capacity_kwh: float, pv_kwp: float | None
) -> list[dict[str, Any]]:
    """Scenarios to price: current, +5/+10 kWh of battery, and +2 kWp of PV when the installed kWp is known."""
    out = [{"name": "current", "capacity_kwh": battery_capacity_kwh, "pv_scale": 1.0}]
    for extra in UPGRADE_BATTERY_STEPS_KWH:
        out.append({"name": f"+{extra:g} kWh battery", "capacity_kwh": battery_capacity_kwh + extra,
                    "pv_scale": 1.0, "added_kwh": extra})
    if _is_number(pv_kwp) and pv_kwp > 0:
        out.append({"name": f"+{UPGRADE_PV_STEP_KWP:g} kWp PV", "capacity_kwh": battery_capacity_kwh,
                    "pv_scale": (pv_kwp + UPGRADE_PV_STEP_KWP) / pv_kwp, "added_kwp": UPGRADE_PV_STEP_KWP})
    return out


def upgrade_advisor(
    days: dict[str, dict[str, dict[str, Any]]],
    prices_pence: dict[str, float | None],
    export_bands: list[tuple[int, int, float]],
    battery_capacity_kwh: float,
    charge_kw: float,
    wear_pence: float | None = None,
    pv_kwp: float | None = None,
    battery_price_per_kwh: float | None = None,
    pv_price_per_kwp: float | None = None,
) -> dict[str, Any]:
    """What +5/+10 kWh of battery (or +2 kWp of PV) would have saved over logged days, at today's prices.

    Each scenario's saving = simulated current cost − simulated scenario cost,
    on the same days and policy, so the model's bias largely cancels. Per
    month and in total; annualised = mean daily saving × 365 (says which months
    it covers: winter-only data understates PV). Payback in years when a
    price is given and the saving is positive; otherwise None. Fewer than
    `UPGRADE_MIN_DAYS` usable days → `"scenarios": []`.
    """
    scenarios = upgrade_scenarios(battery_capacity_kwh, pv_kwp)
    per_day: dict[str, list[float]] = {}
    used_dates: list[str] = []
    for day, snaps in sorted(days.items()):
        costs = [
            simulate_system(snaps, prices_pence, export_bands, battery_capacity_kwh, s["capacity_kwh"],
                            charge_kw, s["pv_scale"], wear_pence)["cost_gbp"]
            for s in scenarios
        ]
        if any(c is None for c in costs):
            continue
        used_dates.append(day)
        per_day[day] = costs
    months = sorted({d[:7] for d in used_dates})
    report: dict[str, Any] = {"days": len(used_dates), "months": months, "scenarios": []}
    if len(used_dates) < UPGRADE_MIN_DAYS:
        return report
    for i, s in enumerate(scenarios[1:], start=1):
        savings = {d: per_day[d][0] - per_day[d][i] for d in used_dates}
        total = sum(savings.values())
        by_month = {m: round(sum(v for d, v in savings.items() if d.startswith(m)), 2) for m in months}
        annual = total / len(used_dates) * 365
        if "added_kwh" in s:
            price = battery_price_per_kwh * s["added_kwh"] if _is_number(battery_price_per_kwh) else None
        else:
            price = pv_price_per_kwp * s["added_kwp"] if _is_number(pv_price_per_kwp) else None
        report["scenarios"].append({
            "name": s["name"],
            "saving_gbp": round(total, 2),
            "by_month_gbp": by_month,
            "annual_gbp": round(annual, 2),
            "price_gbp": round(price, 2) if price else None,
            "payback_years": round(price / annual, 1) if price and annual > 0 else None,
        })
    return report


def upgrade_summary(report: dict[str, Any]) -> str | None:
    """One line per scenario for the notification; None when there's nothing to say."""
    if not report.get("scenarios"):
        return None
    months = report["months"]
    span = months[0] if len(months) == 1 else f"{months[0]} to {months[-1]}"
    lines = [f"Replayed {report['days']} logged days ({span}) at today's rates:"]
    for s in report["scenarios"]:
        payback = f"; payback ~{s['payback_years']:g} yrs at £{s['price_gbp']:,.0f}" if s["payback_years"] else ""
        lines.append(f"• {s['name']}: saved £{s['saving_gbp']:.2f} (~£{s['annual_gbp']:.0f}/yr){payback}")
    lines.append("Upper-bound estimate (perfect-foresight charging); data only covers the months listed.")
    return "\n".join(lines)


# Bill reconciliation (phase 36).
BILL_GAP_ALERT_GBP = 2.0

BILL_MIN_COVERAGE = 25 / 30  # under this share of days logged, the projection is not trusted (phase 85)


def logged_day_cost_gbp(day: dict[str, Any], include_gas: bool) -> float | None:
    """A logged day's net £ (import − export, + gas when configured); None when a needed field is missing."""
    imp, exp, gas = (day.get(k) for k in ("actual_import_cost_gbp", "actual_export_income_gbp", "actual_gas_cost_gbp"))
    if not _is_number(imp) or not _is_number(exp):
        return None
    if not include_gas:
        return round(imp - exp, 2)
    return net_cost_gbp(imp, exp, gas) if _is_number(gas) else None


BILL_ATTRIBUTE_KEYS = (
    "logged_gbp", "billed_gbp", "gap_gbp", "gap_pct", "days_logged", "days_in_month", "partial", "alert", "includes_gas",
    "projected_logged_gbp", "gap_per_day_pct", "coverage_low",
)


def bill_attributes(state: dict[str, Any] | None) -> dict[str, Any]:
    """Billed-amount entity attributes: every key always present (None until a bill is reconciled)."""
    state = state or {}
    result = state.get("result") or {}
    return {"month": state.get("month"), **{k: result.get(k) for k in BILL_ATTRIBUTE_KEYS}}


def bill_period(today: date, start_day: Any = 1) -> tuple[date, date, str]:
    """The latest complete bill period before ``today``: (first day, last day, label).

    Periods run from ``start_day`` (clamped 1-28) to the day before it next month. Day 1 is the
    previous calendar month, labelled ``YYYY-MM``; other days are labelled ``YYYY-MM-DD to YYYY-MM-DD``.
    """
    try:
        day = min(28, max(1, int(float(start_day))))
    except (TypeError, ValueError):
        day = 1

    def month_start(year: int, month: int) -> date:
        return date(year + (month - 1) // 12, (month - 1) % 12 + 1, day)

    next_start = month_start(today.year, today.month if today.day >= day else today.month - 1)
    start = month_start(next_start.year, next_start.month - 1)
    end = next_start - timedelta(days=1)
    label = start.strftime("%Y-%m") if day == 1 else f"{start.isoformat()} to {end.isoformat()}"
    return start, end, label


def reconcile_month(
    day_costs: list[float | None],
    days_in_month: int,
    billed_gbp: float | None,
    standing_p_per_day: float | None = None,
    threshold_gbp: float = BILL_GAP_ALERT_GBP,
) -> dict[str, Any]:
    """Compare a month's logged cost with the billed £.

    ``day_costs`` is one value per logged day (None = that day's cost is
    missing). ``standing_p_per_day`` is added per day only when the logged
    costs exclude it (None = already included, as the Octopus cost sensors
    do). A partial month is compared on its projected cost (logged / days
    logged × days in the period, phase 85); with under ``BILL_MIN_COVERAGE``
    of days logged it is ``coverage_low`` and never alerts. Unknown billed →
    gap None, no alert.
    """
    values = [c for c in day_costs if _is_number(c)]
    logged = sum(values) + (standing_p_per_day or 0.0) * len(values) / 100 if values else None
    partial = len(values) < days_in_month
    gap = None if logged is None or billed_gbp is None else billed_gbp - logged
    pct = None if gap is None or not billed_gbp else gap / abs(billed_gbp) * 100
    projected = logged * days_in_month / len(values) if values else None
    proj_gap = None if projected is None or billed_gbp is None else billed_gbp - projected
    per_day_pct = None if proj_gap is None or not billed_gbp else proj_gap / abs(billed_gbp) * 100
    coverage_low = days_in_month > 0 and len(values) / days_in_month < BILL_MIN_COVERAGE
    return {
        "logged_gbp": round_or_none(logged),
        "billed_gbp": round_or_none(billed_gbp),
        "gap_gbp": round_or_none(gap),
        "gap_pct": round_or_none(pct, 1),
        "days_logged": len(values),
        "days_in_month": days_in_month,
        "partial": partial,
        "alert": proj_gap is not None and not coverage_low and abs(proj_gap) > threshold_gbp,
        "projected_logged_gbp": round_or_none(projected),
        "gap_per_day_pct": round_or_none(per_day_pct, 1),
        "coverage_low": coverage_low,
    }


def meter_reconciliation(
    inverter_kwh: float | None,
    slots: Any,
    tolerance_pct: float = 8.0,
    min_diff_kwh: float = 0.5,
    through_slot: int = 44,
) -> dict[str, Any] | None:
    """Compare the inverter's grid kWh with the smart meter's half-hourly slots (phase 44).

    The inverter total is read at 22:00, so only the first ``through_slot`` slots
    (00:00-22:00) are summed. None when either side is missing or the slots are not
    48 numbers (rule 11: never guess). ``diverged`` needs both a % gap and an
    absolute gap, so a tiny day cannot trip it on rounding alone.
    """
    if inverter_kwh is None or not isinstance(slots, (list, tuple)) or len(slots) != HALF_HOURS_PER_DAY:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in slots):
        return None
    meter = float(sum(slots[:through_slot]))
    diff = float(inverter_kwh) - meter
    pct = diff / meter * 100.0 if meter >= 1.0 else None
    diverged = abs(diff) >= min_diff_kwh and (pct is None or abs(pct) >= tolerance_pct)
    return {
        "inverter_kwh": round(float(inverter_kwh), 2),
        "meter_kwh": round(meter, 2),
        "diff_kwh": round(diff, 2),
        "pct": None if pct is None else round(pct, 1),
        "diverged": diverged,
    }


def divergence_streak(history: list[dict[str, Any]]) -> int:
    """Length of the trailing run of consecutive-date diverged days in ``history``.

    ``history`` holds ``{"date": "YYYY-MM-DD", "diverged": bool}`` in any order; a
    gap in dates or a non-diverged day ends the run.
    """
    days = sorted(
        ((date.fromisoformat(str(h["date"])), bool(h.get("diverged"))) for h in history if h.get("date")),
        key=lambda x: x[0],
    )
    run = 0
    prev: date | None = None
    for d, bad in reversed(days):
        if prev is not None and (prev - d).days != 1:
            break
        if not bad:
            break
        run += 1
        prev = d
    return run


# Household electrification and sizing advisor (phase 78). Report only: extends the phase 50
# upgrade report with a heat pump on Cosy vs gas, and a higher inverter limit (clipped solar).
HEAT_PUMP_COP_RANGE = (2.0, 4.5)

HEAT_PUMP_COP_AT_0C = 2.8

HEAT_PUMP_COP_PER_C = 0.08

HEAT_PUMP_COP_DEFAULT = 2.8  # a day with no logged outside temperature

BOILER_EFFICIENCY = 0.9

ELECTRIFY_MIN_DAYS = 14


def heat_pump_cop(temp_c: float | None) -> float:
    """Seasonal air-source COP at a daily mean outside temperature (linear, clamped); a default without one."""
    if not _is_number(temp_c):
        return HEAT_PUMP_COP_DEFAULT
    low, high = HEAT_PUMP_COP_RANGE
    return round(max(low, min(high, HEAT_PUMP_COP_AT_0C + HEAT_PUMP_COP_PER_C * temp_c)), 2)


def mean_band_price(bands: list[tuple[int, int, float]]) -> float | None:
    """Time-weighted mean p/kWh over the 48 half-hours of a day; None if any half-hour has no price."""
    prices = [band_price(bands, i * 30) for i in range(HALF_HOURS_PER_DAY)]
    if not prices or any(p is None for p in prices):
        return None
    return round(sum(prices) / len(prices), 2)


def _annual_block(savings: dict[str, float], price_gbp: float | None) -> dict[str, Any]:
    total = sum(savings.values())
    annual = total / len(savings) * 365 if savings else 0.0
    return {
        "days": len(savings),
        "months": sorted({d[:7] for d in savings}),
        "saving_gbp": round(total, 2),
        "annual_gbp": round(annual, 2),
        "price_gbp": round(price_gbp, 2) if _is_number(price_gbp) and price_gbp > 0 else None,
        "payback_years": round(price_gbp / annual, 1) if _is_number(price_gbp) and price_gbp > 0 and annual > 0 else None,
    }


def heat_pump_scenario(
    days: list[dict[str, Any]],
    gas_unit_pence: float | None,
    hp_price_pence: float | None,
    gas_standing_pence: float | None = None,
    install_price_gbp: float | None = None,
    temps: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """What replacing gas with an air-source heat pump would have saved on the logged days.

    Per day: gas kWh = (gas cost − standing) ÷ unit price; heat = gas kWh ×
    boiler efficiency; heat-pump kWh = heat ÷ COP(the day's outside °C);
    saving = gas cost (standing included: the gas supply is assumed capped
    off) − heat-pump kWh × `hp_price_pence` (the Cosy all-day mean). Away
    days and days without a gas cost are skipped. None without a gas unit
    price, a heat-pump price, or `ELECTRIFY_MIN_DAYS` usable days.
    Confidence is always "low": COP, boiler efficiency and the heating share
    of gas (hot water and cooking are counted as heat) are assumptions.
    """
    if not (_is_number(gas_unit_pence) and gas_unit_pence > 0 and _is_number(hp_price_pence)):
        return None
    standing = gas_standing_pence if _is_number(gas_standing_pence) else 0.0
    temps = temps or {}
    savings: dict[str, float] = {}
    gas_kwh = hp_kwh = 0.0
    cops: list[float] = []
    for day in days:
        date_str, cost = day.get("date"), day.get("actual_gas_cost_gbp")
        if not date_str or day.get("away") or not _is_number(cost):
            continue
        kwh = max(0.0, cost * 100 - standing) / gas_unit_pence
        cop = heat_pump_cop(temps.get(date_str))
        elec = kwh * BOILER_EFFICIENCY / cop
        savings[date_str] = (cost * 100 - elec * hp_price_pence) / 100
        gas_kwh += kwh
        hp_kwh += elec
        cops.append(cop)
    if len(savings) < ELECTRIFY_MIN_DAYS:
        return None
    return {
        **_annual_block(savings, install_price_gbp),
        "gas_kwh": round(gas_kwh, 1),
        "heat_pump_kwh": round(hp_kwh, 1),
        "mean_cop": round(sum(cops) / len(cops), 2),
        "hp_price_p": hp_price_pence,
        "confidence": "low",
        "assumptions": f"COP {HEAT_PUMP_COP_RANGE[0]:g}–{HEAT_PUMP_COP_RANGE[1]:g} by outside temp, "
                       f"boiler {BOILER_EFFICIENCY:.0%}, all gas treated as heat, gas standing charge dropped, "
                       "Cosy all-day mean price (no shifting into cheap windows)",
    }


def export_limit_scenario(
    days: list[dict[str, Any]], export_pence: float | None, price_gbp: float | None = None
) -> dict[str, Any] | None:
    """What a higher inverter limit would have earned: the logged clipped solar sold at the midday export price.

    Clipping is estimated from the PV string power near the limit (phase 29),
    so treat it as indicative ("medium" confidence). None without an export
    price or `ELECTRIFY_MIN_DAYS` days with a clipping figure.
    """
    if not _is_number(export_pence):
        return None
    savings = {
        d["date"]: d["clipped_solar_kwh"] * export_pence / 100
        for d in days if d.get("date") and _is_number(d.get("clipped_solar_kwh"))
    }
    if len(savings) < ELECTRIFY_MIN_DAYS:
        return None
    return {
        **_annual_block(savings, price_gbp),
        "clipped_kwh": round(sum(d["clipped_solar_kwh"] for d in days if d.get("date") in savings), 2),
        "export_p": export_pence,
        "confidence": "medium",
    }


def electrification_summary(report: dict[str, Any]) -> list[str]:
    """Notification lines for the heat-pump and inverter-limit scenarios (empty when neither ran)."""
    lines = []
    hp = report.get("heat_pump")
    if hp:
        payback = f"; payback ~{hp['payback_years']:g} yrs" if hp.get("payback_years") else ""
        if hp["saving_gbp"] >= 0:
            outcome = f"saved £{hp['saving_gbp']:.2f} over {hp['days']} days (~£{hp['annual_gbp']:.0f}/yr"
        else:
            outcome = f"cost £{-hp['saving_gbp']:.2f} more over {hp['days']} days (~£{-hp['annual_gbp']:.0f}/yr more"
        lines.append(
            f"• Heat pump on Cosy instead of gas: {outcome} at the logged season's rate{payback}; "
            f"low confidence: {hp['assumptions']})"
        )
    limit = report.get("export_limit")
    if limit:
        lines.append(
            f"• Higher inverter limit: {limit['clipped_kwh']:g} kWh clipped solar would have earned "
            f"£{limit['saving_gbp']:.2f} (~£{limit['annual_gbp']:.0f}/yr)"
        )
    return lines


def upgrade_options(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every priced option in an upgrade advisor report as flat rows, best annual £ first (dashboard).

    Rows: {name, annual_gbp, saving_gbp, payback_years, days, confidence}. Battery/PV
    scenarios, the heat pump (phase 78) and the inverter limit; missing ones are skipped.
    """
    if not isinstance(report, dict):
        return []
    rows = [
        {"name": s.get("name"), "annual_gbp": s.get("annual_gbp"), "saving_gbp": s.get("saving_gbp"),
         "payback_years": s.get("payback_years"), "days": report.get("days"), "confidence": "upper bound"}
        for s in report.get("scenarios") or [] if isinstance(s, dict)
    ]
    for key, name in (("heat_pump", "Heat pump (Cosy) vs gas"), ("export_limit", "Higher inverter limit")):
        block = report.get(key)
        if isinstance(block, dict):
            rows.append({"name": name, "annual_gbp": block.get("annual_gbp"), "saving_gbp": block.get("saving_gbp"),
                         "payback_years": block.get("payback_years"), "days": block.get("days"),
                         "confidence": block.get("confidence")})
    rows = [r for r in rows if _is_number(r["annual_gbp"])]
    return sorted(rows, key=lambda r: -r["annual_gbp"])
