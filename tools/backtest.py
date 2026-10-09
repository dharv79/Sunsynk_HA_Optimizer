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
    name = f"{_PKG}.{filename[:-3]}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, _PKG_DIR / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


const = _load("const.py")
flux_helpers = _load("flux_helpers.py")
planning = _load("planning.py")


def load_records(data_dir: str) -> list[dict[str, Any]]:
    """Every parseable record in the monthly JSONL files (read-only)."""
    records: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(data_dir, "*.jsonl"))):
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


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data_dir", help="sunsynk_optimizer_data directory (read-only)")
    parser.add_argument("--target-offset", type=int, default=0, help="percentage points added to targets below 100")
    parser.add_argument("--load-kw", type=float, default=None, help="alternative overnight load for solar_bridge nights")
    parser.add_argument("--capacity", type=float, default=const.DEFAULT_BATTERY_CAPACITY, help="battery kWh")
    parser.add_argument("--days", type=int, default=None, help="only the most recent N nights")
    args = parser.parse_args(argv)
    records = load_records(args.data_dir)
    report = {
        "plan": replay(records, args.target_offset, args.load_kw, args.capacity, days=args.days),
        "forecast_correction": forecast_error(records, args.days),
    }
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
