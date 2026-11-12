#!/usr/bin/env python3
# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Offline backtest of the overnight plan against logged history (phase 14).

Replays logged `import_plan` + `morning_state` nights with alternative
parameters and estimates the change in off-peak import, day-rate shortfall
and cost. Also scores the live vs weighted (phase 20) forecast correction
against actual solar. Read-only on the data dir; no Home Assistant needed.

    python3 tools/backtest.py /config/sunsynk_optimizer_data --target-offset -5
    python3 tools/backtest.py DATA_DIR --load-kw 0.6 --days 60
    python3 tools/backtest.py DATA_DIR --upgrade --pv-kwp 4.2   # phase 50 upgrade advisor
    python3 tools/backtest.py DATA_DIR --planner mpc            # phase 69 MPC headroom
    python3 tools/backtest.py DATA_DIR --load-profile           # phase 71 peak-load error

Model (approximate, for comparing settings, not absolute figures):
- The battery charges from the plan's start SOC to its target; the alternative
  target moves the charge-end SOC and the 06:00 SOC by the same amount.
- Off-peak kWh = SOC charged × capacity / logged charge_efficiency (1.0 on
  history before phase 13).
- Day-rate shortfall = SOC below the 20% reserve at 06:00 × capacity, bought
  at the day rate.
- `--load-kw` re-sizes only plain `solar_bridge` nights that logged
  `hours_to_solar`; other nights take just `--target-offset`.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
_PKG = "_sunsynk_backtest"


def _load(filename: str):
    """Load an HA-free integration module by path under a private package name."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PKG_DIR)]
        sys.modules[_PKG] = pkg
    name = f"{_PKG}.{filename.split('/')[0].removesuffix('.py')}"
    if name not in sys.modules:
        path = _PKG_DIR / filename
        kwargs = {"submodule_search_locations": [str(path.parent)]} if filename.endswith("__init__.py") else {}
        spec = importlib.util.spec_from_file_location(name, path, **kwargs)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


const = _load("const.py")
flux_helpers = _load("flux_helpers.py")
planning = _load("planning/__init__.py")


def load_records(data_dir: str) -> list[dict[str, Any]]:
    """Every parseable record in the monthly JSONL files (read-only)."""
    records: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.jsonl"))):
        if os.path.basename(path).startswith("mpc-"):
            continue  # shadow MPC plans live in their own files and are not replayed
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    return records


def _by_date(records: list[dict[str, Any]], record_type: str) -> dict[str, dict[str, Any]]:
    """First record per date (matches the logger's write-time dedup)."""
    out: dict[str, dict[str, Any]] = {}
    for rec in records:
        if rec.get("type") == record_type and rec.get("date"):
            out.setdefault(rec["date"], rec)
    return out


def _recent(dates: list[str], days: int | None) -> list[str]:
    dates = sorted(dates)
    return dates[-days:] if days else dates


def replay(
    records: list[dict[str, Any]],
    target_offset: int = 0,
    load_kw: float | None = None,
    capacity_kwh: float = const.DEFAULT_BATTERY_CAPACITY,
    prices_pence: dict[str, float | None] | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Baseline vs alternative totals over the logged nights."""
    prices = prices_pence or flux_helpers.kpi_prices_pence(flux_helpers.default_charges())
    plans, mornings = _by_date(records, "import_plan"), _by_date(records, "morning_state")
    reserve = planning.KPI_RESERVE_SOC
    totals = {"offpeak_kwh": [0.0, 0.0], "shortfall_kwh": [0.0, 0.0]}
    nights = skipped = load_resized = 0
    for date in _recent([d for d in plans if d in mornings], days):
        plan, morning = plans[date], mornings[date]
        soc, target, morning_soc = plan.get("soc"), plan.get("target_soc"), morning.get("morning_soc")
        if None in (soc, target, morning_soc) or plan.get("away"):
            skipped += 1
            continue
        alt = target
        if target < 100:
            alt += target_offset
            hours, logged_load = plan.get("hours_to_solar"), plan.get("avg_consumption_kw")
            if (
                load_kw is not None and plan.get("target_soc_reason") == "solar_bridge"
                and hours is not None and logged_load is not None
            ):
                alt += hours * (load_kw - logged_load) / capacity_kwh * 100
                load_resized += 1
        alt = max(0.0, min(100.0, alt))
        efficiency = plan.get("charge_efficiency") or 1.0
        for i, tgt in enumerate((target, alt)):
            end_soc = max(soc, tgt)
            totals["offpeak_kwh"][i] += (end_soc - soc) / 100 * capacity_kwh / efficiency
            morning_i = morning_soc + (end_soc - max(soc, target))
            totals["shortfall_kwh"][i] += max(0.0, reserve - morning_i) / 100 * capacity_kwh
        nights += 1

    def _cost(i: int) -> float | None:
        if prices.get("offpeak") is None or prices.get("day") is None:
            return None
        pence = totals["offpeak_kwh"][i] * prices["offpeak"] + totals["shortfall_kwh"][i] * prices["day"]
        return round(pence / 100, 2)

    base_cost, alt_cost = _cost(0), _cost(1)
    return {
        "nights": nights,
        "skipped": skipped,
        "load_resized_nights": load_resized,
        "params": {"target_offset": target_offset, "load_kw": load_kw, "capacity_kwh": capacity_kwh},
        "baseline": {
            "offpeak_kwh": round(totals["offpeak_kwh"][0], 2),
            "shortfall_kwh": round(totals["shortfall_kwh"][0], 2),
            "cost_gbp": base_cost,
        },
        "alternative": {
            "offpeak_kwh": round(totals["offpeak_kwh"][1], 2),
            "shortfall_kwh": round(totals["shortfall_kwh"][1], 2),
            "cost_gbp": alt_cost,
        },
        "cost_delta_gbp": None if base_cost is None or alt_cost is None else round(alt_cost - base_cost, 2),
    }


def _paired(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{date: import_plan fields + day_actuals fields} for dates with both."""
    plans, actuals = _by_date(records, "import_plan"), _by_date(records, "day_actuals")
    return {d: {**plans[d], **actuals[d]} for d in sorted(set(plans) & set(actuals))}


def forecast_error(records: list[dict[str, Any]], days: int | None = None) -> dict[str, Any]:
    """Mean absolute error (kWh) of raw × live vs raw × weighted correction against actual solar."""
    plans, actuals = _by_date(records, "import_plan"), _by_date(records, "day_actuals")
    errors: dict[str, list[float]] = {"live": [], "weighted": []}
    for date in _recent([d for d in plans if d in actuals], days):
        raw, actual = plans[date].get("raw_forecast_kwh"), actuals[date].get("actual_solar_kwh")
        live, weighted = plans[date].get("forecast_correction_factor"), plans[date].get("forecast_correction_weighted")
        if None in (raw, actual, live, weighted):
            continue
        errors["live"].append(abs(raw * live - actual))
        errors["weighted"].append(abs(raw * weighted - actual))
    n = len(errors["live"])
    return {
        "days": n,
        "live_mae_kwh": round(sum(errors["live"]) / n, 2) if n else None,
        "weighted_mae_kwh": round(sum(errors["weighted"]) / n, 2) if n else None,
    }


def _snapshot_days(records: list[dict[str, Any]], days: int | None) -> dict[str, dict[str, dict[str, Any]]]:
    dates = _recent(sorted({r["date"] for r in records if r.get("type") == "meter_snapshot" and r.get("date")}), days)
    out: dict[str, dict[str, dict[str, Any]]] = {date: {} for date in dates}
    for rec in records:
        if rec.get("type") == "meter_snapshot" and rec.get("date") in out and rec.get("time"):
            out[rec["date"]].setdefault(rec["time"], rec)
    return out


def _band_prices(args: argparse.Namespace) -> dict[str, float]:
    return {"offpeak": args.offpeak_p, "day": args.day_p, "peak": args.peak_p}


def mpc_headroom(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    """Phase 69: metered 02:00-22:00 cost vs the MPC with hindsight, per logged day."""
    per_day = {}
    for date, snaps in _snapshot_days(records, args.days).items():
        out = planning.mpc_backtest_day(snaps, _band_prices(args), [(0, 1440, args.export_p)], args.capacity, args.charge_kw)
        if out["headroom_gbp"] is not None:
            per_day[date] = out
    total = round(sum(d["headroom_gbp"] for d in per_day.values()), 2)
    return {"days": len(per_day), "headroom_gbp": total, "per_day": per_day}


def upgrade(records: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    """Phase 50: replay logged days with a bigger battery or more PV, at the given flat band prices."""
    days = _snapshot_days(records, args.days)
    return planning.upgrade_advisor(
        days, _band_prices(args), [(0, 1440, args.export_p)], args.capacity, args.charge_kw,
        pv_kwp=args.pv_kwp, battery_price_per_kwh=args.battery_price, pv_price_per_kwp=args.pv_price,
    )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data_dir", help="sunsynk_optimizer_data directory (read-only)")
    parser.add_argument("--target-offset", type=int, default=0, help="percentage points added to targets below 100")
    parser.add_argument("--load-kw", type=float, default=None, help="alternative overnight load for solar_bridge nights")
    parser.add_argument("--capacity", type=float, default=const.DEFAULT_BATTERY_CAPACITY, help="battery kWh")
    parser.add_argument("--days", type=int, default=None, help="only the most recent N nights")
    parser.add_argument("--upgrade", action="store_true", help="phase 50: price a bigger battery / more PV instead")
    parser.add_argument("--charge-kw", type=float, default=const.DEFAULT_CHARGE_RATE, help="--upgrade: charge rate")
    parser.add_argument("--pv-kwp", type=float, default=None, help="--upgrade: installed PV kWp (adds the PV scenario)")
    parser.add_argument("--battery-price", type=float, default=None, help="--upgrade: £ per extra battery kWh")
    parser.add_argument("--pv-price", type=float, default=None, help="--upgrade: £ per extra PV kWp")
    parser.add_argument("--offpeak-p", type=float, default=16.66, help="--upgrade: off-peak import p/kWh")
    parser.add_argument("--day-p", type=float, default=27.77, help="--upgrade: day import p/kWh")
    parser.add_argument("--peak-p", type=float, default=38.88, help="--upgrade: peak import p/kWh")
    parser.add_argument("--export-p", type=float, default=15.0, help="--upgrade: flat export p/kWh")
    parser.add_argument("--load-profile", action="store_true", help="phase 71: score the load profile's 16-19 kWh")
    parser.add_argument("--planner", choices=("rules", "mpc"), default="rules", help="mpc: phase 69 headroom report")
    args = parser.parse_args(argv)
    records = load_records(args.data_dir)
    if args.load_profile:
        half_import = {
            r["date"]: r["kwh"] for r in records
            if r.get("type") == "half_hourly_import" and r.get("date") and isinstance(r.get("kwh"), list)
        }
        report = planning.load_profile_backtest(_snapshot_days(records, args.days), half_import)
        print(json.dumps(report, indent=2))
        return report
    if args.planner == "mpc":
        report = mpc_headroom(records, args)
        print(json.dumps(report, indent=2))
        return report
    if args.upgrade:
        report = upgrade(records, args)
        print(json.dumps(report, indent=2))
        return report
    samples = planning.forecast_ratio_samples(list(_paired(records).values()))
    report = {
        "forecast_quantile_coverage": planning.forecast_quantile_coverage(samples),
        "plan": replay(records, args.target_offset, args.load_kw, args.capacity, days=args.days),
        "forecast_correction": forecast_error(records, args.days),
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
