"""Phase 50: battery and PV upgrade advisor (simulate_system / upgrade_advisor)."""

from __future__ import annotations

from conftest import _planning as planning

PRICES = {"offpeak": 10.0, "day": 30.0, "peak": 40.0}
EXPORT = [(0, 1440, 15.0)]


def _snaps(**override):
    snaps = {
        "02:00": {"load_kwh": 1.0, "grid_import_kwh": 0.0, "grid_export_kwh": 0.0, "soc": 50},
        "05:00": {"load_kwh": 2.0, "grid_import_kwh": 6.0, "grid_export_kwh": 0.0, "soc": 100},
        "16:00": {"load_kwh": 7.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 100},
        "19:00": {"load_kwh": 10.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 70},
        "22:00": {"load_kwh": 12.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 50},
    }
    for time, fields in override.items():
        snaps[time.replace("h", ":")] = {**snaps[time.replace("h", ":")], **fields}
    return snaps


def test_zero_battery_reproduces_phase_37_baseline():
    base = planning.no_battery_baseline(_snaps(), PRICES, EXPORT, 10.0)
    sim = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 0.0, 3.0)
    assert sim["cost_gbp"] == base["no_battery_gbp"] == 1.90
    assert sim["cycled_kwh"] == 0.0


def test_battery_charges_off_peak_for_later_deficits():
    sim = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 10.0, 3.0)
    # Deficits: peak 3 kWh, evening 2 kWh; 05-16 surplus 2 kWh refills. Usable 7.5 kWh.
    assert sim["cost_gbp"] < 1.90
    # Grid charge only covers what the solar can't: no export left over at the bounded size.
    assert sim["export_kwh"] == 0.0


def test_bigger_battery_never_costs_more_and_saturates():
    costs = [planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, c, 3.0)["cost_gbp"] for c in (0, 5, 10, 15, 20)]
    assert costs == sorted(costs, reverse=True)
    assert costs[-1] == costs[-2]  # the day's deficit is already covered


def test_charge_rate_limits_the_overnight_top_up():
    slow = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 20.0, 0.5)
    fast = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 20.0, 5.0)
    assert slow["cost_gbp"] > fast["cost_gbp"]


def test_more_pv_lowers_cost():
    one = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 10.0, 3.0)
    more = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 10.0, 3.0, pv_scale=1.5)
    assert more["cost_gbp"] < one["cost_gbp"]


def test_no_grid_charge_when_not_worth_it():
    flat = {"offpeak": 30.0, "day": 30.0, "peak": 30.0}
    sim = planning.simulate_system(_snaps(), flat, EXPORT, 10.0, 10.0, 3.0)
    assert sim["import_kwh"] > 0 and sim["cycled_kwh"] == 2.0  # solar surplus only


def test_missing_data_is_none():
    snaps = _snaps()
    del snaps["16:00"]
    assert planning.simulate_system(snaps, PRICES, EXPORT, 10.0, 10.0, 3.0)["cost_gbp"] is None
    assert planning.simulate_system(_snaps(), {}, EXPORT, 10.0, 0.0, 3.0)["cost_gbp"] is None


def test_wear_adds_cost():
    plain = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 10.0, 3.0)
    worn = planning.simulate_system(_snaps(), PRICES, EXPORT, 10.0, 10.0, 3.0, wear_pence=4.0)
    assert worn["cost_gbp"] > plain["cost_gbp"]


def _days(n, month="2026-10"):
    # A winter-ish day where 10 kWh can't cover the evening: the advisor has something to find.
    heavy = _snaps(**{"19h00": {"load_kwh": 13.0, "grid_import_kwh": 9.0},
                      "22h00": {"load_kwh": 18.0, "grid_import_kwh": 12.0}})
    return {f"{month}-{i + 1:02d}": heavy for i in range(n)}


def test_advisor_needs_enough_days():
    out = planning.upgrade_advisor(_days(3), PRICES, EXPORT, 10.0, 3.0)
    assert out["days"] == 3 and out["scenarios"] == []


def test_advisor_scenarios_saving_and_payback():
    out = planning.upgrade_advisor(_days(10), PRICES, EXPORT, 10.0, 5.0, pv_kwp=4.0,
                                   battery_price_per_kwh=300.0, pv_price_per_kwp=None)
    names = [s["name"] for s in out["scenarios"]]
    assert names == ["+5 kWh battery", "+10 kWh battery", "+2 kWp PV"]
    plus5 = out["scenarios"][0]
    assert plus5["saving_gbp"] > 0
    assert plus5["by_month_gbp"] == {"2026-10": plus5["saving_gbp"]}
    assert plus5["price_gbp"] == 1500.0
    assert plus5["payback_years"] == round(1500.0 / plus5["annual_gbp"], 1)
    assert out["scenarios"][2]["payback_years"] is None  # no PV price given
    assert out["months"] == ["2026-10"]


def test_advisor_skips_pv_without_kwp_and_summary():
    out = planning.upgrade_advisor(_days(10), PRICES, EXPORT, 10.0, 5.0)
    assert len(out["scenarios"]) == 2
    text = planning.upgrade_summary(out)
    assert "+5 kWh battery" in text and "2026-10" in text
    assert planning.upgrade_summary({"scenarios": []}) is None


def test_backtest_upgrade_cli(tmp_path, capsys):
    import importlib.util
    import json
    from pathlib import Path

    lines = []
    for date, snaps in _days(8).items():
        for time, snap in snaps.items():
            lines.append({"type": "meter_snapshot", "date": date, "time": time, **snap})
    (tmp_path / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    path = Path(__file__).resolve().parent.parent / "tools" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_tool_upgrade", path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    report = bt.main([str(tmp_path), "--upgrade", "--capacity", "10", "--charge-kw", "5",
                      "--offpeak-p", "10", "--day-p", "30", "--peak-p", "40", "--export-p", "15"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["days"] == 8 and report["scenarios"][0]["saving_gbp"] > 0
