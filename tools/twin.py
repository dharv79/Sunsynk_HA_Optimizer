#!/usr/bin/env python3
# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Digital twin (phase 72): replay logged days through the real planner and a half-hourly battery model.

Read-only on the data dir; no Home Assistant needed. Run it first with no
sweep to check calibration (`soc_06_mae` / `soc_22_mae`, the model's SOC error
against the logs), then sweep parameters:

    python3 tools/twin.py DATA_DIR
    python3 tools/twin.py DATA_DIR --grid target_offset=-10,-5,0,5 --grid load_scale=0.9,1,1.1
    python3 tools/twin.py DATA_DIR --days 60 --csv twin.csv

Parameters: capacity_kwh, charge_kw, floor_soc, charge_eff, discharge_eff,
target_offset, load_scale. Costs are an approximation for ranking settings, not
a bill. Flat band prices (override with --offpeak-p/--day-p/--peak-p/--export-p).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest  # noqa: E402  (loads the HA-free integration modules)

planning = backtest.planning


def parse_grid(items: list[str]) -> dict[str, list[float]]:
    """['a=1,2', 'b=3'] → {'a': [1.0, 2.0], 'b': [3.0]}."""
    grid: dict[str, list[float]] = {}
    for item in items:
        name, _, values = item.partition("=")
        grid[name.strip()] = [float(v) for v in values.split(",") if v.strip()]
    return grid


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data_dir", help="sunsynk_optimizer_data directory (read-only)")
    parser.add_argument("--days", type=int, default=None, help="only the most recent N days")
    parser.add_argument("--grid", action="append", default=[], help="name=v1,v2,… (repeatable; combinations are all run)")
    parser.add_argument("--set", action="append", default=[], help="name=value base parameter (repeatable)")
    parser.add_argument("--offpeak-p", type=float, default=16.66)
    parser.add_argument("--day-p", type=float, default=27.77)
    parser.add_argument("--peak-p", type=float, default=38.88)
    parser.add_argument("--export-p", type=float, default=15.0)
    parser.add_argument("--csv", default=None, help="write the per-day rows (or the sweep ranking) to this file")
    args = parser.parse_args(argv)
    records = backtest.load_records(args.data_dir)
    days = backtest._snapshot_days(records, args.days)
    plans, mornings = backtest._by_date(records, "import_plan"), backtest._by_date(records, "morning_state")
    prices = {"offpeak": args.offpeak_p, "day": args.day_p, "peak": args.peak_p}
    export_bands = [(0, 1440, args.export_p)]
    base = {k: v[0] for k, v in parse_grid(args.set).items()}
    if args.grid:
        rows = planning.twin_sweep(days, plans, mornings, prices, export_bands, parse_grid(args.grid), base)
        report = {"ranking": rows}
        flat = [{**r["params"], **{k: v for k, v in r.items() if k != "params"}} for r in rows]
    else:
        run = planning.twin_replay(days, plans, mornings, prices, export_bands, base)
        report = {k: v for k, v in run.items() if k != "rows"}
        flat = run["rows"]
    print(json.dumps(report, indent=2))
    if args.csv and flat:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
    return report


if __name__ == "__main__":
    main()
