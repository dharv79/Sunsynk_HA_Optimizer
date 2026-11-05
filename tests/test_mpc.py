"""Phase 69: receding-horizon MPC planner (shadow)."""

from __future__ import annotations

from conftest import _planning as planning

PRICES = {"offpeak": 10.0, "day": 30.0, "peak": 40.0}
EXPORT = [(0, 1440, 15.0)]
LOADS = {"overnight": 0.5, "day": 0.6, "evening": 1.2}


def _slot(minute, imp, exp=0.0, load=0.5, solar=0.0):
    return {"minute": minute, "day": 0, "import_p": imp, "export_p": exp, "load_kwh": load, "solar_kwh": solar}


def test_charges_cheap_and_discharges_dear():
    slots = [_slot(120 + 30 * i, 10.0) for i in range(6)] + [_slot(960 + 30 * i, 40.0, load=1.0) for i in range(6)]
    plan = planning.mpc_solve(slots, 25, 10.0, 3.0)
    actions = [s["action"] for s in plan["slots"]]
    assert "grid_charge" in actions[:6]
    assert set(actions[6:]) <= {"discharge", "hold"} and "discharge" in actions[6:]
    # Only charges what the evening needs: ends near the floor, not full.
    assert plan["slots"][-1]["soc"] <= 30


def test_holds_when_nothing_pays():
    slots = [_slot(600 + 30 * i, 30.0) for i in range(8)]
    plan = planning.mpc_solve(slots, 60, 10.0, 3.0)
    assert {s["action"] for s in plan["slots"]} <= {"hold", "discharge"}
    flat = planning.mpc_solve([_slot(600, 30.0, load=0.0)], 60, 10.0, 3.0)
    assert flat["slots"][0]["action"] == "hold"


def test_wear_stops_marginal_arbitrage():
    slots = [_slot(120, 10.0, load=0.0), _slot(960, 13.0, load=1.0)]
    assert planning.mpc_solve(slots, 25, 10.0, 3.0)["slots"][0]["action"] == "grid_charge"
    assert planning.mpc_solve(slots, 25, 10.0, 3.0, wear_pence=5.0)["slots"][0]["action"] == "hold"


def test_never_below_floor_and_respects_rate():
    slots = [_slot(960 + 30 * i, 40.0, load=2.0) for i in range(6)]
    plan = planning.mpc_solve(slots, 60, 10.0, 3.0)
    assert min(s["soc"] for s in plan["slots"]) >= planning.BATTERY_LOW_SOC
    socs = [60] + [s["soc"] for s in plan["slots"]]
    max_down = int(3.0 * 0.5 / planning.DISCHARGE_EFFICIENCY / 10.0 * 100)
    assert all(a - b <= max_down for a, b in zip(socs, socs[1:]))


def test_soaks_solar_and_exports_surplus_at_peak():
    slots = [_slot(720, 30.0, exp=5.0, load=0.2, solar=2.0), _slot(990, 40.0, exp=30.0, load=0.2)]
    plan = planning.mpc_solve(slots, 25, 10.0, 3.0)
    assert plan["slots"][0]["action"] == "solar_charge"
    assert plan["slots"][1]["action"] == "export"


def test_terminal_value_keeps_charge():
    slots = [_slot(600, 30.0, exp=5.0, load=0.0)]
    assert planning.mpc_solve(slots, 80, 10.0, 3.0, terminal_pence=12.0)["slots"][0]["action"] == "hold"


def test_missing_inputs_are_none():
    assert planning.mpc_solve([], 50, 10.0, 3.0) is None
    assert planning.mpc_solve([_slot(0, 10.0)], None, 10.0, 3.0) is None
    assert planning.mpc_slots(600, {"day": 30.0}, EXPORT, {}, None, LOADS) is None  # no peak price


def test_slots_horizon_and_inputs():
    hourly = {12: 2.0}
    slots = planning.mpc_slots(605, PRICES, EXPORT, hourly, None, LOADS)
    assert slots[0]["minute"] == 600
    # No tomorrow forecast: stops at 06:00 tomorrow.
    assert slots[-1]["minute"] == 330 and slots[-1]["day"] == 1
    noon = next(s for s in slots if s["minute"] == 720)
    assert noon["solar_kwh"] == 1.0 and noon["load_kwh"] == 0.3
    assert next(s for s in slots if s["minute"] == 1020)["import_p"] == 40.0
    full = planning.mpc_slots(605, PRICES, EXPORT, hourly, {}, LOADS)
    assert len(full) == planning.MPC_MAX_SLOTS
    profile = [0.1] * 48
    assert planning.mpc_slots(605, PRICES, EXPORT, hourly, {}, LOADS, profile)[0]["load_kwh"] == 0.1


def test_summary():
    slots = [_slot(120 + 30 * i, 10.0) for i in range(6)] + [_slot(960 + 30 * i, 40.0, load=1.0) for i in range(6)]
    summary = planning.mpc_summary(planning.mpc_solve(slots, 25, 10.0, 3.0))
    assert summary["first_action"] == "grid_charge" and summary["soc_0500"] is not None
    assert summary["offpeak_charge_kwh"] == summary["grid_charge_kwh"] > 0
    assert planning.mpc_summary(None) == {}


def _snaps():
    return {
        "02:00": {"load_kwh": 1.0, "grid_import_kwh": 0.0, "grid_export_kwh": 0.0, "soc": 50},
        "05:00": {"load_kwh": 2.0, "grid_import_kwh": 6.0, "grid_export_kwh": 0.0, "soc": 100},
        "16:00": {"load_kwh": 7.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 100},
        "19:00": {"load_kwh": 10.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 70},
        "22:00": {"load_kwh": 12.0, "grid_import_kwh": 6.0, "grid_export_kwh": 2.0, "soc": 50},
    }


def test_backtest_day_headroom():
    out = planning.mpc_backtest_day(_snaps(), PRICES, EXPORT, 10.0, 3.0)
    assert out["headroom_gbp"] == round(out["actual_gbp"] - out["mpc_gbp"], 2)
    del_snaps = _snaps()
    del del_snaps["22:00"]
    assert planning.mpc_backtest_day(del_snaps, PRICES, EXPORT, 10.0, 3.0)["headroom_gbp"] is None


def test_backtest_planner_mpc_cli(tmp_path, capsys):
    import importlib.util
    import json
    from pathlib import Path

    lines = [{"type": "meter_snapshot", "date": f"2026-10-0{d}", "time": t, **snap}
             for d in (1, 2) for t, snap in _snaps().items()]
    (tmp_path / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    path = Path(__file__).resolve().parent.parent / "tools" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_tool_mpc", path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    report = bt.main([str(tmp_path), "--planner", "mpc", "--capacity", "10"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["days"] == 2 and isinstance(report["headroom_gbp"], float)
