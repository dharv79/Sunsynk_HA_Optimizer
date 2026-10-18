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


# ---------------------------------------------------------------- phase 15

from datetime import date as _date

TODAY = _date(2026, 10, 20)


def _fb_day(i, **overrides):
    day = {
        "date": f"2026-10-{19 - i:02d}", "forecast_band": "shoulder", "is_full_day": False,
        "evening_export_disabled": False, "away": False, "target_soc": 50,
        "grid_import_morning_kwh": 0.0, "unused_charge_kwh": 0.0,
    }
    day.update(overrides)
    return day


def test_day_kpis_morning_band():
    assert planning.day_kpis(_snaps(), PRICES, 10.0, 0.5)["grid_import_morning_kwh"] == 1.0


def test_import_feedback_raises_on_day_rate_import():
    days = [_fb_day(i, grid_import_morning_kwh=1.2) for i in range(5)]
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False) == (5, 5, "day_rate_import")


def test_import_feedback_lowers_on_unused_charge_without_import():
    days = [_fb_day(i, grid_import_morning_kwh=0.1, unused_charge_kwh=1.5) for i in range(6)]
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False) == (-5, 6, "unused_charge")


def test_import_feedback_neutral_when_on_target():
    days = [_fb_day(i, grid_import_morning_kwh=0.2, unused_charge_kwh=0.3) for i in range(5)]
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False) == (0, 5, "on_target")


def test_import_feedback_below_min_days_is_zero():
    days = [_fb_day(i, grid_import_morning_kwh=2.0) for i in range(4)]
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False) == (0, 4, "insufficient_days")


@pytest.mark.parametrize("override", [
    {"forecast_band": "summer_like"},
    {"is_full_day": True},
    {"evening_export_disabled": True},
    {"away": True},
    {"target_soc": 100},
    {"grid_import_morning_kwh": None},
    {"date": "2026-10-20"},  # today
    {"date": "2026-10-01"},  # older than 14 days
])
def test_import_feedback_exclusions(override):
    days = [_fb_day(i, grid_import_morning_kwh=2.0) for i in range(4)]
    days.append(_fb_day(4, **{"grid_import_morning_kwh": 2.0, **override}))
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False)[0] == 0


def test_import_feedback_away_regime_uses_away_days():
    days = [_fb_day(i, away=True, grid_import_morning_kwh=2.0) for i in range(5)]
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", True)[0] == 5
    assert planning.import_feedback_adjustment(days, TODAY, "shoulder", False)[0] == 0


def test_day_kpis_evening_band():
    assert planning.day_kpis(_snaps(), PRICES, 10.0, 0.5)["grid_import_evening_kwh"] == 0.3


# ---------------------------------------------------------------- phase 17

def test_arbitrage_worth_it_margin_and_missing():
    # 16.66 / 0.85 = 19.6p replacement cost.
    assert planning.arbitrage_worth_it(16.66, 27.81) is True
    assert planning.arbitrage_worth_it(16.66, 19.5) is False
    assert planning.arbitrage_worth_it(16.66, 27.81, wear_pence=9.0) is False  # wear tips it
    assert planning.arbitrage_worth_it(None, 27.81) is None
    assert planning.arbitrage_worth_it(16.66, None) is None


def test_peak_export_surplus_exports_down_to_reserve_plus_margin():
    plan = planning.peak_export_plan(95, 73, 10.0, 27.81, 16.66)
    assert plan["decision"] == "export"
    assert plan["target_soc"] == 78
    assert plan["export_kwh"] == 1.7
    assert plan["export_gbp"] == round(1.7 * 27.81 / 100, 2)
    assert plan["gain_gbp"] == round(1.7 * (27.81 - 16.66 / 0.85) / 100, 2)


def test_peak_export_no_surplus():
    plan = planning.peak_export_plan(80, 73, 10.0, 27.81, 16.66)  # 0.2 kWh above 78%
    assert plan["decision"] == "no_surplus"
    assert plan["export_kwh"] == 0.0
    assert plan["gain_gbp"] is None


def test_peak_export_not_worth_it_and_no_price():
    assert planning.peak_export_plan(95, 73, 10.0, 15.0, 16.66)["decision"] == "not_worth_it"
    plan = planning.peak_export_plan(95, 73, 10.0, None, 16.66)
    assert plan["decision"] == "no_price"
    assert plan["export_gbp"] is None


def test_peak_export_target_capped_at_100():
    assert planning.peak_export_plan(100, 98, 10.0, 27.81, 16.66)["target_soc"] == 100


def test_peak_export_dedups_and_pairs_export_days_only(tmp_path):
    dl = object.__new__(_data_logger.DataLogger)
    dl._data_dir = str(tmp_path)
    rec = {"type": "peak_export", "date": "2026-10-09", "decision": "export", "export_kwh": 1.7,
           "gain_gbp": 0.14, "live": False}
    dl._write_record(rec)
    dl._write_record(dict(rec, export_kwh=9.9))
    records = [json.loads(l) for f in tmp_path.glob("*.jsonl") for l in f.read_text().splitlines()]
    assert [r["export_kwh"] for r in records] == [1.7]
    base = [
        {"type": "import_plan", "date": "2026-10-09", "solar_forecast_kwh": 10.0, "target_soc": 50, "soc": 30},
        {"type": "day_actuals", "date": "2026-10-09", "evening_soc": 40.0, "actual_solar_kwh": 8.0},
        {"type": "import_plan", "date": "2026-10-10", "solar_forecast_kwh": 10.0, "target_soc": 50, "soc": 30},
        {"type": "day_actuals", "date": "2026-10-10", "evening_soc": 40.0, "actual_solar_kwh": 8.0},
        {"type": "peak_export", "date": "2026-10-10", "decision": "no_surplus", "export_kwh": 0.0},
    ]
    days = {d["date"]: d for d in dl._pair_records(records + base)}
    assert days["2026-10-09"]["peak_export_gain_gbp"] == 0.14
    assert days["2026-10-10"]["peak_export_kwh"] is None


# ---------------------------------------------------------------- phase 24

def test_wear_cost_pence_per_kwh():
    # £3,000 over 12,000 kWh of throughput = 25p/kWh.
    assert planning.wear_cost_pence_per_kwh(3000, 12000) == 25.0
    assert planning.wear_cost_pence_per_kwh(2400, 24000) == 10.0


def test_wear_cost_blank_or_bad_inputs_is_none_not_zero():
    for cost, life in ((None, 12000), (3000, None), (0, 12000), (3000, 0), (-5, 100), (3000, -1)):
        assert planning.wear_cost_pence_per_kwh(cost, life) is None


def test_wear_tips_arbitrage_at_the_margin():
    # Break-even sell price = 16.66 / 0.85 + wear = 19.6 + 5 = 24.6p.
    assert planning.arbitrage_worth_it(16.66, 24.7, wear_pence=5.0) is True
    assert planning.arbitrage_worth_it(16.66, 24.5, wear_pence=5.0) is False
    assert planning.arbitrage_worth_it(16.66, 24.5) is True  # no wear term → as before
    assert planning.arbitrage_worth_it(None, 24.7, wear_pence=5.0) is None


def test_peak_export_plan_respects_and_logs_wear():
    base = planning.peak_export_plan(95, 73, 10.0, 27.81, 16.66)
    assert base["decision"] == "export" and base["wear_cost_p"] is None
    worn = planning.peak_export_plan(95, 73, 10.0, 27.81, 16.66, wear_pence=9.0)
    assert worn["decision"] == "not_worth_it" and worn["wear_cost_p"] == 9.0
    cheap = planning.peak_export_plan(95, 73, 10.0, 27.81, 16.66, wear_pence=2.0)
    assert cheap["decision"] == "export"
    assert cheap["gain_gbp"] < base["gain_gbp"]


# --- phase 25: spare solar ---------------------------------------------------

def test_cloudy_day_has_no_spare_solar(planning):
    res = planning.expected_spare_solar({h: 0.2 for h in range(8, 16)}, 1.0, 0.4, 0.0)
    assert res["total_kwh"] == 0 and res["window"] is None


def test_full_battery_all_surplus_is_spare(planning):
    hourly = {h: 1.5 for h in range(10, 14)}
    res = planning.expected_spare_solar(hourly, 1.0, 0.5, 0.0)
    assert res["total_kwh"] == 4.0
    assert res["window"] == {"start": "10:00", "end": "13:00", "kwh": 3.0}


def test_empty_battery_refills_first(planning):
    hourly = {h: 2.0 for h in range(10, 14)}  # 1.5 kWh surplus an hour = 6 kWh
    res = planning.expected_spare_solar(hourly, 1.0, 0.5, 4.0)
    assert res["total_kwh"] == 2.0
    # The first hours refill the battery; spare is at the end of the day.
    assert res["window"]["end"] == "14:00"


def test_no_forecast_is_none_not_zero(planning):
    assert planning.expected_spare_solar(None, 1.0, 0.5, 0.0)["total_kwh"] is None
    assert planning.expected_spare_solar({}, 1.0, 0.5, 0.0)["window"] is None


# ---- Phase 26: overnight baseload alert ----

def _nights(*vals, away=False):
    return [{"load_kwh": v, "away": away} for v in vals]


def test_baseload_needs_history(planning):
    assert planning.baseload_anomaly(_nights(2.0, 2.2, 2.1), 6.0) is None


def test_baseload_normal_night_no_alert(planning):
    assert planning.baseload_anomaly(_nights(*[2.2] * 8), 2.6) is None


def test_baseload_anomaly_alerts(planning):
    res = planning.baseload_anomaly(_nights(2.0, 2.2, 2.4, 2.2, 2.1, 2.3, 2.2, 2.2), 4.1)
    assert res == {"tonight_kwh": 4.1, "usual_kwh": 2.2, "nights": 8}


def test_baseload_small_base_needs_min_extra(planning):
    # 2x but only +0.6 kWh: noise, no alert.
    assert planning.baseload_anomaly(_nights(*[0.6] * 8), 1.2) is None


def test_baseload_ignores_away_and_none(planning):
    hist = _nights(*[2.2] * 6) + _nights(0.3, 0.3, 0.3, away=True) + [{"load_kwh": None, "away": False}]
    # Only 6 usable home nights -> not enough history.
    assert planning.baseload_anomaly(hist, 6.0) is None
    assert planning.baseload_anomaly(hist + _nights(2.2), 6.0)["usual_kwh"] == 2.2
    assert planning.baseload_anomaly(hist + _nights(2.2), None) is None


# ---- Phase 40: daytime load spike alert ----


def test_daytime_import_sustained_alerts(planning):
    out = planning.daytime_import_anomaly([1800.0, 2100.0], 60)
    assert out == {"grid_w": 1800, "soc": 60, "checks": 2}


def test_daytime_import_single_spike_no_alert(planning):
    assert planning.daytime_import_anomaly([200.0, 2500.0], 60) is None
    assert planning.daytime_import_anomaly([2500.0], 60) is None


def test_daytime_import_low_soc_expected(planning):
    assert planning.daytime_import_anomaly([2000.0, 2000.0], planning.BATTERY_LOW_SOC + 5) is None


def test_daytime_import_missing_sensors(planning):
    assert planning.daytime_import_anomaly([None, 2000.0], 60) is None
    assert planning.daytime_import_anomaly([2000.0, 2000.0], None) is None


def test_daytime_import_away_threshold(planning):
    assert planning.daytime_import_anomaly([900.0, 900.0], 60) is None
    assert planning.daytime_import_anomaly([900.0, 900.0], 60, planning.DAYTIME_IMPORT_AWAY_THRESHOLD_W)


# ---- Phase 38: cost of 1 kWh now ----

_PRICES = {"offpeak": 17.0, "day": 28.0, "peak": 39.0, "export_peak": 29.0}
_EXPORT = [(2 * 60, 5 * 60, 7.0), (5 * 60, 16 * 60, 15.0), (16 * 60, 19 * 60, 29.0), (19 * 60, 2 * 60, 15.0)]


def test_marginal_cost_solar(planning):
    out = planning.marginal_cost_pence(12 * 60, 100, 40, True, _PRICES, _EXPORT)
    assert out == {"pence": 15.0, "source": "solar"}


def test_marginal_cost_battery_replacement(planning):
    out = planning.marginal_cost_pence(12 * 60, 80, 40, False, _PRICES, _EXPORT, wear_pence=5.0)
    assert out["source"] == "battery"
    assert out["pence"] == round(17.0 / planning.ROUND_TRIP_EFFICIENCY + 5.0, 2)


def test_marginal_cost_battery_peak_export_floor(planning):
    out = planning.marginal_cost_pence(17 * 60, 80, 40, False, _PRICES, _EXPORT)
    assert out == {"pence": 29.0, "source": "battery"}


def test_marginal_cost_grid_below_reserve_and_offpeak(planning):
    assert planning.marginal_cost_pence(12 * 60, 30, 40, False, _PRICES, _EXPORT) == {"pence": 28.0, "source": "grid"}
    assert planning.marginal_cost_pence(3 * 60, 90, 40, False, _PRICES, _EXPORT) == {"pence": 17.0, "source": "grid"}
    assert planning.marginal_cost_pence(17 * 60, None, 40, False, _PRICES, _EXPORT)["pence"] == 39.0


def test_marginal_cost_missing_price_is_none(planning):
    assert planning.marginal_cost_pence(12 * 60, 30, 40, False, {"day": None}, _EXPORT)["pence"] is None
    assert planning.marginal_cost_pence(12 * 60, 30, 40, True, _PRICES, [])["pence"] is None
    assert planning.marginal_cost_pence(12 * 60, 80, 40, False, {"offpeak": None}, _EXPORT)["pence"] is None


def test_solar_is_spare(planning):
    assert planning.solar_is_spare(-2000, 3000)
    assert not planning.solar_is_spare(-2000, 500)   # battery exporting
    assert not planning.solar_is_spare(-50, 3000)
    assert not planning.solar_is_spare(None, 3000)


def test_cheapest_hour_today(planning):
    assert planning.cheapest_hour_today(20, _PRICES, _EXPORT) == {"hour": "20:00", "pence": 28.0, "source": "grid"}
    out = planning.cheapest_hour_today(9, _PRICES, _EXPORT, [{"hour": 13, "spare_kwh": 1.2}, {"hour": 14, "spare_kwh": 0}])
    assert out == {"hour": "13:00", "pence": 15.0, "source": "solar"}
    assert planning.cheapest_hour_today(9, {}, _EXPORT) is None


# ---- Phase 37: value of the system ----

_BASE_PRICES = {"offpeak": 10.0, "day": 30.0, "peak": 40.0}
_BASE_EXPORT = [(0, 1440, 15.0)]


def _base_snaps(**override):
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


def test_no_battery_baseline_per_band(planning):
    out = planning.no_battery_baseline(_base_snaps(), _BASE_PRICES, _BASE_EXPORT, 10.0)
    # Baseline: 00-02 1 kWh day (30p), 02-05 1 kWh offpeak (10p), 05-16 solar 7 covers load 5 and
    # exports 2 (-30p), 16-19 3 kWh peak (120p; solar 0), 19-22 2 kWh day (60p) = 190p.
    assert out["no_battery_gbp"] == 1.90
    # Actual: 6 kWh offpeak (60p) - 2 kWh export (30p) = 30p.
    assert out["with_battery_gbp"] == 0.30
    assert out["battery_saving_gbp"] == 1.60
    assert out["battery_cycled_kwh"] == 5.0


def test_no_battery_baseline_wear_subtracted(planning):
    out = planning.no_battery_baseline(_base_snaps(), _BASE_PRICES, _BASE_EXPORT, 10.0, wear_pence=4.0)
    assert out["battery_saving_gbp"] == round(1.60 - 5.0 * 4.0 / 100, 2)


def test_no_battery_baseline_missing_snapshot_excluded(planning):
    snaps = _base_snaps()
    del snaps["16:00"]
    out = planning.no_battery_baseline(snaps, _BASE_PRICES, _BASE_EXPORT, 10.0)
    assert out["battery_saving_gbp"] is None
    snaps = _base_snaps(**{"19h00": {"soc": None}})
    assert planning.no_battery_baseline(snaps, _BASE_PRICES, _BASE_EXPORT, 10.0)["battery_saving_gbp"] is None


def test_no_battery_baseline_no_prices_is_none(planning):
    assert planning.no_battery_baseline(_base_snaps(), {}, _BASE_EXPORT, 10.0)["no_battery_gbp"] is None


def test_battery_value_summary(planning):
    days = [{"battery_saving_gbp": 1.5}, {"battery_saving_gbp": None}, {"battery_saving_gbp": 0.75}, {}]
    assert planning.battery_value_summary(days) == {"saving_gbp": 2.25, "days": 2}
    assert planning.battery_value_summary([{}]) == {"saving_gbp": None, "days": 0}


# ---- Phase 34: battery capacity tracking ----


def _cap_snaps(imp02=0.0, imp05=5.0, soc02=40, soc05=80, load02=1.0, load05=1.6):
    return {
        "02:00": {"grid_import_kwh": imp02, "grid_export_kwh": 0.0, "load_kwh": load02, "soc": soc02},
        "05:00": {"grid_import_kwh": imp05, "grid_export_kwh": 0.0, "load_kwh": load05, "soc": soc05},
        "19:00": {"grid_import_kwh": imp05, "grid_export_kwh": 0.0, "load_kwh": 10.0, "soc": 90},
        "22:00": {"grid_import_kwh": imp05, "grid_export_kwh": 0.0, "load_kwh": 14.5, "soc": 40},
    }


def test_capacity_clean_charge_sample(planning):
    out = planning.capacity_samples(_cap_snaps(), month=7)  # summer: no discharge sample
    assert out == [{"kind": "charge", "kwh_per_100": round(4.4 * planning.CHARGE_EFFICIENCY / 40 * 100, 2), "swing_soc": 40.0}]


def test_capacity_discharge_sample_in_winter(planning):
    out = planning.capacity_samples(_cap_snaps(), month=12)
    discharge = [s for s in out if s["kind"] == "discharge"][0]
    assert discharge["kwh_per_100"] == round(4.5 / planning.CHARGE_EFFICIENCY / 50 * 100, 2)


def test_capacity_small_swing_excluded(planning):
    assert planning.capacity_samples(_cap_snaps(soc05=60), month=7) == []


def test_capacity_missing_meter_no_sample(planning):
    snaps = _cap_snaps()
    snaps["05:00"]["load_kwh"] = None
    assert planning.capacity_samples(snaps, month=7) == []


def test_capacity_estimate_median_needs_samples(planning):
    samples = [{"kwh_per_100": v} for v in (9.0, 9.4, 9.6, 9.2, 30.0)]
    assert planning.capacity_estimate(samples) == 9.4
    assert planning.capacity_estimate(samples[:4]) is None
    assert planning.capacity_estimate([]) is None


def test_capacity_low(planning):
    assert planning.capacity_low(8.5, 9.58)
    assert not planning.capacity_low(9.0, 9.58)
    assert not planning.capacity_low(None, 9.58)


def test_capacity_estimate_pending(planning):
    assert planning.capacity_estimate_pending(None)
    assert planning.capacity_estimate_pending({})
    assert planning.capacity_estimate_pending({"estimate_kwh": None, "samples": 4})
    assert not planning.capacity_estimate_pending({"estimate_kwh": 9.4, "samples": 5})


def test_record_for_date(planning):
    rec = {"date": "2026-10-14", "decision": "export"}
    assert planning.record_for_date(rec, "2026-10-14") is rec
    assert planning.record_for_date(rec, "2026-10-15") == {}
    assert planning.record_for_date({}, "2026-10-14") == {}
    assert planning.record_for_date(None, "2026-10-14") == {}


def test_peak_export_skip_reason(planning):
    assert planning.peak_export_skip_reason(True, None) == "skipped_slots_held"
    assert planning.peak_export_skip_reason(False, None) == "skipped_no_soc"
    assert planning.peak_export_skip_reason(False, 0.0) is None


def test_meter_delta(planning):
    assert planning.meter_delta({"grid_export": 5.0}, {"grid_export": 7.26}, "grid_export") == 2.26
    assert planning.meter_delta({"grid_export": 5.0}, {"grid_export": 0.3}, "grid_export") is None  # midnight reset
    assert planning.meter_delta(None, {"grid_export": 1.0}, "grid_export") is None
    assert planning.meter_delta({"grid_export": None}, {"grid_export": 1.0}, "grid_export") is None


def test_free_event_result_and_report(planning):
    event = {
        "soc_at_sell_start": 80.0,
        "sell_start": "2026-10-20T09:30:00+01:00",
        "meters_at_sell_start": {"grid_export": 1.0, "grid_import": 3.0},
        "meters_at_free_start": {"grid_export": 3.5, "grid_import": 3.2},
        "free_start": "2026-10-20T12:00:00+01:00",
    }
    r = planning.free_event_result(event, {"grid_import": 8.2}, 100.0, 27.77)
    assert r == {"sold_kwh": 2.5, "free_import_kwh": 5.0, "free_import_value_gbp": 1.39, "soc_at_end": 100.0}
    line = planning.event_report("free_event", "done", {**event, **r})
    assert line["type"] == "free_event" and line["phase"] == "done" and line["sold_kwh"] == 2.5
    assert line["sell_start"] == event["sell_start"]
    assert not any(k.startswith("meters_") for k in line)


def test_free_event_result_no_sell(planning):
    # 18/10: SOC stayed below the 40% floor, so no sell ran; it reported sold_kwh null
    # and the stale scheduling estimate (sell_start 11:00, skip_sell true).
    event = {
        "sell_start": "2026-10-18T11:00:00+01:00", "skip_sell": True, "expected_export_kwh": 0.0,
        "meters_at_free_start": {"grid_export": 0.2, "grid_import": 4.0},
        "free_start": "2026-10-18T11:00:00+01:00",
    }
    r = planning.free_event_result(event, {"grid_import": 12.1}, 100.0, 27.77)
    assert r["sold_kwh"] == 0.0 and r["free_import_kwh"] == 8.1 and r["free_import_value_gbp"] == 2.25
    line = planning.event_report("free_event", "done", {**event, **r})
    assert not set(planning.FREE_EVENT_ESTIMATE_KEYS) & line.keys()


def test_bill_attributes_always_has_every_key(planning):
    empty = planning.bill_attributes(None)
    assert set(empty) == {"month", *planning.BILL_ATTRIBUTE_KEYS}
    assert all(v is None for v in empty.values())
    done = planning.bill_attributes(
        {"month": "2026-09", "billed_gbp": 60.0, "result": {"logged_gbp": 58.1, "gap_gbp": 1.9, "days_logged": 30}}
    )
    assert (done["month"], done["logged_gbp"], done["gap_gbp"], done["days_logged"]) == ("2026-09", 58.1, 1.9, 30)
    assert done["alert"] is None


# ---- Phase 36: bill reconciliation ----


def test_reconcile_matching_month(planning):
    out = planning.reconcile_month([1.0] * 30, 30, 30.5)
    assert out["logged_gbp"] == 30.0 and out["gap_gbp"] == 0.5 and not out["partial"] and not out["alert"]


def test_reconcile_gap_over_threshold(planning):
    out = planning.reconcile_month([1.0] * 30, 30, 35.0)
    assert out["alert"] and out["gap_gbp"] == 5.0 and out["gap_pct"] == round(5 / 35 * 100, 1)


def test_reconcile_missing_days_partial_no_alert(planning):
    out = planning.reconcile_month([1.0] * 25 + [None], 30, 40.0)
    assert out["partial"] and out["days_logged"] == 25 and not out["alert"]


def test_reconcile_billed_unknown(planning):
    out = planning.reconcile_month([1.0] * 30, 30, None)
    assert out["gap_gbp"] is None and not out["alert"]
    assert planning.reconcile_month([], 30, 20.0)["logged_gbp"] is None


def test_reconcile_standing_added_when_excluded(planning):
    assert planning.reconcile_month([1.0] * 30, 30, 50.0, standing_p_per_day=50.0)["logged_gbp"] == 45.0


def test_logged_day_cost(planning):
    day = {"actual_import_cost_gbp": 2.0, "actual_export_income_gbp": 0.5, "actual_gas_cost_gbp": 1.0}
    assert planning.logged_day_cost_gbp(day, include_gas=True) == 2.5
    assert planning.logged_day_cost_gbp(day, include_gas=False) == 1.5
    assert planning.logged_day_cost_gbp({**day, "actual_gas_cost_gbp": None}, include_gas=True) is None
    assert planning.logged_day_cost_gbp({"actual_import_cost_gbp": 2.0}, include_gas=False) is None
