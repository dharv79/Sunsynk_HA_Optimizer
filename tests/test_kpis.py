"""Phase 14: meter snapshots, efficiency KPIs and the offline backtest."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from conftest import _data_logger, _flux_helpers, _planning as planning

PRICES = {"offpeak": 16.66, "day": 27.77, "peak": 38.88, "export_peak": 27.81}


def _snaps(**overrides):
    base = {
        "02:00": {"grid_import_kwh": 0.5, "grid_export_kwh": 0.0, "load_kwh": 1.0, "soc": 30},
        "05:00": {"grid_import_kwh": 6.5, "grid_export_kwh": 0.0, "load_kwh": 2.5, "soc": 80},
        "16:00": {"grid_import_kwh": 7.5, "grid_export_kwh": 3.0, "load_kwh": 9.0, "soc": 90},
        "19:00": {"grid_import_kwh": 7.7, "grid_export_kwh": 5.0, "load_kwh": 12.0, "soc": 60},
        "22:00": {"grid_import_kwh": 8.0, "grid_export_kwh": 5.0, "load_kwh": 14.0, "soc": 40},
    }
    for time, fields in overrides.items():
        base[time.replace("_", ":")] = fields
    return base


def test_day_kpis_bands_and_prices():
    k = planning.day_kpis(_snaps(), PRICES, 10.0, 0.5)
    assert k["grid_import_offpeak_kwh"] == 6.0
    assert k["grid_import_peak_kwh"] == 0.2
    assert k["grid_import_day_kwh"] == 1.8  # 0.5 before 02:00 + 1.0 + 0.3
    assert k["self_sufficiency_pct"] == pytest.approx(42.9)
    assert k["avoidable_import_gbp"] == round((1.8 * 27.77 + 0.2 * 38.88) / 100, 2)
    assert k["export_peak_kwh"] == 2.0
    assert k["export_peak_gbp"] == round(2.0 * 27.81 / 100, 2)


def test_unused_charge_capped_at_overnight_added():
    # Need at 16:00: 20% + (5 kWh evening + 2 kWh late) / 10 kWh = 90% → no spare.
    assert planning.day_kpis(_snaps(), PRICES, 10.0, 0.5)["unused_charge_kwh"] == 0.0
    # Lighter evening: need 20 + (1 + 2)/10×100 = 50% → spare 40% = 4 kWh, capped at
    # the 5 kWh the night added → 4.0; with a small night top-up the cap bites.
    light = _snaps(**{"22_00": {"grid_import_kwh": 8.0, "grid_export_kwh": 5.0, "load_kwh": 10.0, "soc": 70}})
    assert planning.day_kpis(light, PRICES, 10.0, 0.5)["unused_charge_kwh"] == 4.0
    small_night = dict(light, **{"05:00": dict(light["05:00"], soc=40)})
    assert planning.day_kpis(small_night, PRICES, 10.0, 0.5)["unused_charge_kwh"] == 1.0


def test_missing_snapshot_gives_none_never_zero():
    snaps = _snaps()
    del snaps["05:00"]
    k = planning.day_kpis(snaps, PRICES, 10.0, 0.5)
    assert k["grid_import_offpeak_kwh"] is None
    assert k["grid_import_day_kwh"] is None
    assert k["avoidable_import_gbp"] is None
    assert k["unused_charge_kwh"] is None
    # Bands that don't need 05:00 still compute.
    assert k["grid_import_peak_kwh"] == 0.2
    assert k["self_sufficiency_pct"] is not None


def test_missing_meter_value_and_missing_price_give_none():
    snaps = _snaps(**{"19_00": {"grid_import_kwh": None, "grid_export_kwh": 5.0, "load_kwh": 12.0, "soc": 60}})
    k = planning.day_kpis(snaps, PRICES, 10.0, 0.5)
    assert k["grid_import_peak_kwh"] is None
    assert k["avoidable_import_gbp"] is None
    k = planning.day_kpis(_snaps(), dict(PRICES, export_peak=None), 10.0, None)
    assert k["export_peak_gbp"] is None
    assert k["unused_charge_kwh"] is None


def test_meter_going_backwards_is_none():
    snaps = _snaps(**{"05:00": {"grid_import_kwh": 0.1, "grid_export_kwh": 0.0, "load_kwh": 2.5, "soc": 80}})
    assert planning.day_kpis(snaps, PRICES, 10.0, 0.5)["grid_import_offpeak_kwh"] is None


def test_zero_load_self_sufficiency_none():
    snaps = _snaps(**{"22_00": {"grid_import_kwh": 8.0, "grid_export_kwh": 5.0, "load_kwh": 0.0, "soc": 40}})
    assert planning.day_kpis(snaps, PRICES, 10.0, 0.5)["self_sufficiency_pct"] is None


def test_week_kpis_sums_and_averages():
    days = [
        {"grid_import_day_kwh": 1.0, "avoidable_import_gbp": 0.3, "self_sufficiency_pct": 40.0},
        {"grid_import_day_kwh": 2.0, "avoidable_import_gbp": None, "self_sufficiency_pct": 60.0},
        {},
    ]
    w = planning.week_kpis(days)
    assert w["week_grid_import_day_kwh"] == 3.0
    assert w["week_avoidable_import_gbp"] == 0.3
    assert w["week_self_sufficiency_pct"] == 50.0
    assert w["week_kpi_days"] == 2
    assert w["week_export_peak_kwh"] is None


def test_kpi_prices_from_default_charges():
    prices = _flux_helpers.kpi_prices_pence(_flux_helpers.default_charges())
    assert prices == {"offpeak": 16.66, "day": 27.77, "peak": 38.88, "export_peak": 27.81}
    assert _flux_helpers.kpi_prices_pence([])["day"] is None


def test_meter_snapshot_dedups_per_date_and_time(tmp_path):
    dl = object.__new__(_data_logger.DataLogger)
    dl._data_dir = str(tmp_path)
    for time, imp in (("02:00", 1.0), ("02:00", 9.0), ("05:00", 6.0)):
        dl._write_record({"type": "meter_snapshot", "date": "2026-10-09", "time": time, "grid_import_kwh": imp})
    dl._write_record({"type": "day_kpis", "date": "2026-10-09", "grid_import_day_kwh": 1.0})
    dl._write_record({"type": "day_kpis", "date": "2026-10-09", "grid_import_day_kwh": 2.0})
    records = [json.loads(l) for f in tmp_path.glob("*.jsonl") for l in f.read_text().splitlines()]
    snaps = _data_logger.snapshots_for_date(records, "2026-10-09")
    assert sorted(snaps) == ["02:00", "05:00"]
    assert snaps["02:00"]["grid_import_kwh"] == 1.0
    assert [r["grid_import_day_kwh"] for r in records if r["type"] == "day_kpis"] == [1.0]


def test_pair_records_carries_kpis():
    dl = object.__new__(_data_logger.DataLogger)
    records = [
        {"type": "import_plan", "date": "2026-10-09", "solar_forecast_kwh": 10.0, "target_soc": 50, "soc": 30},
        {"type": "day_actuals", "date": "2026-10-09", "evening_soc": 40.0, "actual_solar_kwh": 8.0},
        {"type": "day_kpis", "date": "2026-10-09", "avoidable_import_gbp": 0.42},
    ]
    day = dl._pair_records(records)[0]
    assert day["avoidable_import_gbp"] == 0.42
    assert day["grid_import_peak_kwh"] is None


# ---------------------------------------------------------------- backtest

def _backtest():
    path = Path(__file__).resolve().parent.parent / "tools" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def data_dir(tmp_path):
    lines = [
        # Plain solar-bridge night: 30 → 50%, 06:00 at 25%.
        {"type": "import_plan", "date": "2026-10-01", "soc": 30, "target_soc": 50,
         "target_soc_reason": "solar_bridge", "hours_to_solar": 3.0, "avg_consumption_kw": 0.5,
         "raw_forecast_kwh": 10.0, "forecast_correction_factor": 1.2, "forecast_correction_weighted": 1.0},
        {"type": "morning_state", "date": "2026-10-01", "morning_soc": 25},
        {"type": "day_actuals", "date": "2026-10-01", "actual_solar_kwh": 10.0},
        # Full-target night (offset doesn't apply above 100).
        {"type": "import_plan", "date": "2026-10-02", "soc": 40, "target_soc": 100,
         "target_soc_reason": "low_solar_override", "charge_efficiency": 0.92},
        {"type": "morning_state", "date": "2026-10-02", "morning_soc": 90},
        # Away night and a night with no morning record are skipped/ignored.
        {"type": "import_plan", "date": "2026-10-03", "soc": 30, "target_soc": 50, "away": True},
        {"type": "morning_state", "date": "2026-10-03", "morning_soc": 25},
        {"type": "import_plan", "date": "2026-10-04", "soc": 30, "target_soc": 50},
    ]
    (tmp_path / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\nnot json\n")
    return tmp_path


def test_backtest_baseline_matches_logged_nights(data_dir):
    bt = _backtest()
    report = bt.replay(bt.load_records(str(data_dir)), capacity_kwh=10.0)
    assert report["nights"] == 2
    assert report["skipped"] == 1
    # 2 kWh + 6 kWh / 0.92
    assert report["baseline"]["offpeak_kwh"] == round(2.0 + 6.0 / 0.92, 2)
    assert report["baseline"] == report["alternative"]
    assert report["cost_delta_gbp"] == 0.0


def test_backtest_lower_target_trades_offpeak_for_shortfall(data_dir):
    bt = _backtest()
    report = bt.replay(bt.load_records(str(data_dir)), target_offset=-10, capacity_kwh=10.0)
    # Night 1: target 40 → 1 kWh less off-peak, 06:00 at 15% → 0.5 kWh shortfall.
    assert report["alternative"]["offpeak_kwh"] == round(report["baseline"]["offpeak_kwh"] - 1.0, 2)
    assert report["alternative"]["shortfall_kwh"] == 0.5
    assert report["cost_delta_gbp"] == round((-1.0 * 16.66 + 0.5 * 27.77) / 100, 2)


def test_backtest_load_kw_resizes_solar_bridge_nights(data_dir):
    bt = _backtest()
    report = bt.replay(bt.load_records(str(data_dir)), load_kw=1.0, capacity_kwh=10.0)
    # 3 h × +0.5 kW = 1.5 kWh = +15% on night 1 only.
    assert report["load_resized_nights"] == 1
    assert report["alternative"]["offpeak_kwh"] == round(report["baseline"]["offpeak_kwh"] + 1.5, 2)


def test_backtest_forecast_error_and_cli(data_dir, capsys):
    bt = _backtest()
    err = bt.forecast_error(bt.load_records(str(data_dir)))
    assert err == {"days": 1, "live_mae_kwh": 2.0, "weighted_mae_kwh": 0.0}
    report = bt.main([str(data_dir), "--target-offset", "5", "--capacity", "10"])
    assert json.loads(capsys.readouterr().out) == report
