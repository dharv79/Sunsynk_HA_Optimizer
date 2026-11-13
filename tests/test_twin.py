"""Phase 72: digital twin (planner + half-hourly battery model) and its CLI."""
import json
import sys
from pathlib import Path

import pytest

PRICES = {"offpeak": 16.66, "day": 27.77, "peak": 38.88}
EXPORT = [(0, 1440, 15.0)]


def _snap(soc, load, imp, exp):
    return {"soc": soc, "load_kwh": load, "grid_import_kwh": imp, "grid_export_kwh": exp}


def _day(soc02=30.0, soc22=40.0):
    """Cumulative meters at 02/05/16/19/22 (daily meters reset at midnight, so 02:00 starts above 0)."""
    return {
        "02:00": _snap(soc02, 1.0, 0.5, 0.0),
        "05:00": _snap(80.0, 2.5, 5.0, 0.0),
        "16:00": _snap(60.0, 9.0, 5.5, 1.0),
        "19:00": _snap(45.0, 12.0, 5.5, 1.0),
        "22:00": _snap(soc22, 14.0, 6.0, 1.0),
    }


PLAN = {"soc": 30.0, "solar_forecast_kwh": 12.0, "raw_forecast_kwh": 14.0, "forecast_band": "summer_like",
        "hours_to_solar": 4.0, "avg_consumption_kw": 0.5}


def test_slots_cover_02_to_22_and_conserve_segment_energy(planning):
    slots = planning.twin_slots(_day(), 10.0)
    assert len(slots) == 40 and slots[0]["minute"] == 120 and slots[-1]["minute"] == 22 * 60 - 30
    assert sum(s["load_kwh"] for s in slots) == pytest.approx(14.0 - 1.0)
    assert all(s["solar_kwh"] >= 0 for s in slots)
    assert planning.twin_slots({"02:00": _snap(30, 1, 0, 0)}, 10.0) is None


def test_target_uses_real_planner_and_offset(planning):
    base = planning.twin_target(PLAN, dict(planning.TWIN_DEFAULTS))
    assert 0 < base <= 100
    shifted = planning.twin_target(PLAN, {**planning.TWIN_DEFAULTS, "target_offset": -5})
    assert shifted == base - 5
    assert planning.twin_target({**PLAN, "avg_consumption_kw": None}, dict(planning.TWIN_DEFAULTS)) is None
    assert planning.twin_target({**PLAN, "solar_forecast_kwh": None, "raw_forecast_kwh": None}, dict(planning.TWIN_DEFAULTS)) is None


def test_run_charges_to_target_then_discharges(planning):
    slots = [{"minute": m, "band": "offpeak" if m < 300 else "day", "load_kwh": 0.2, "solar_kwh": 0.0}
             for m in range(120, 22 * 60, 30)]
    out = planning.twin_run(slots, 30.0, 90.0, PRICES, EXPORT, dict(planning.TWIN_DEFAULTS))
    assert out["soc_06"] > 80 and out["soc_22"] == pytest.approx(25.0)  # drained to the floor by 22:00
    assert out["import_kwh"] > 0 and out["export_kwh"] == 0


def test_run_surplus_charges_then_exports(planning):
    slots = [{"minute": m, "band": "day", "load_kwh": 0.0, "solar_kwh": 1.0} for m in range(600, 700, 30)]
    out = planning.twin_run(slots, 90.0, 90.0, PRICES, EXPORT, dict(planning.TWIN_DEFAULTS))
    assert out["soc_22"] == pytest.approx(100.0) and out["export_kwh"] > 0 and out["cost_gbp"] < 0


def test_run_missing_price_or_soc_is_none(planning):
    slots = [{"minute": 600, "band": "peak", "load_kwh": 1.0, "solar_kwh": 0.0}]
    assert planning.twin_run(slots, 50.0, 50.0, {"offpeak": 16.0}, EXPORT, dict(planning.TWIN_DEFAULTS)) is None
    assert planning.twin_run(slots, None, 50.0, PRICES, EXPORT, dict(planning.TWIN_DEFAULTS)) is None
    assert planning.twin_run([], 50.0, 50.0, PRICES, EXPORT, dict(planning.TWIN_DEFAULTS)) is None


def test_day_skips_away_and_incomplete(planning):
    assert planning.twin_day(_day(), PLAN, PRICES, EXPORT) is not None
    assert planning.twin_day(_day(), {**PLAN, "away": True}, PRICES, EXPORT) is None
    assert planning.twin_day({}, PLAN, PRICES, EXPORT) is None


def test_replay_calibration_and_skips(planning):
    days = {"2026-11-01": _day(), "2026-11-02": _day(), "2026-11-03": {}}
    plans = {d: PLAN for d in days}
    mornings = {"2026-11-01": {"morning_soc": 70.0}}
    out = planning.twin_replay(days, plans, mornings, PRICES, EXPORT)
    assert out["days"] == 2 and out["skipped"] == 1
    assert out["soc_06_mae"] is not None and out["soc_22_mae"] is not None
    assert out["cost_gbp"] == pytest.approx(sum(r["cost_gbp"] for r in out["rows"]), abs=0.01)
    empty = planning.twin_replay({}, {}, {}, PRICES, EXPORT)
    assert empty["cost_gbp"] is None and empty["soc_06_mae"] is None


def test_sweep_ranks_cheapest_first_and_rejects_unknown(planning):
    days = {"2026-11-01": _day()}
    plans = {"2026-11-01": PLAN}
    rows = planning.twin_sweep(days, plans, {}, PRICES, EXPORT, {"target_offset": [-10, 0, 10]})
    assert len(rows) == 3
    assert [r["cost_gbp"] for r in rows] == sorted(r["cost_gbp"] for r in rows)
    base = next(r for r in rows if r["params"]["target_offset"] == 0)
    assert base["delta_gbp"] == 0.0
    with pytest.raises(ValueError):
        planning.twin_sweep(days, plans, {}, PRICES, EXPORT, {"nope": [1]})


def test_cli_end_to_end(tmp_path, capsys):
    lines = [{"type": "import_plan", "date": "2026-11-01", **PLAN}]
    for time, snap in _day().items():
        lines.append({"type": "meter_snapshot", "date": "2026-11-01", "time": time, **snap})
    lines.append({"type": "morning_state", "date": "2026-11-01", "morning_soc": 70.0})
    (tmp_path / "2026-11.jsonl").write_text("\n".join(json.dumps(r) for r in lines))
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    import twin
    report = twin.main([str(tmp_path), "--csv", str(tmp_path / "out.csv")])
    assert report["days"] == 1 and (tmp_path / "out.csv").exists()
    ranked = twin.main([str(tmp_path), "--grid", "target_offset=-5,0"])
    assert len(ranked["ranking"]) == 2
    assert twin.parse_grid(["a=1,2", "b=3"]) == {"a": [1.0, 2.0], "b": [3.0]}
