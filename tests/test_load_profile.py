"""Phase 71: half-hourly household load forecaster."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import _planning as planning


def _snaps(peak=3.0, evening=3.0):
    # 00-02 1.0, 02-05 1.5, 05-16 5.5, 16-19 peak, 19-22 evening
    loads = {"02:00": 1.0, "05:00": 2.5, "16:00": 8.0}
    loads["19:00"] = loads["16:00"] + peak
    loads["22:00"] = loads["19:00"] + evening
    return {t: {"load_kwh": v} for t, v in loads.items()}


def test_day_slots_even_and_22_to_24_repeats_evening():
    slots = planning.day_load_slots(_snaps())
    assert len(slots) == 48
    assert slots[0] == pytest.approx(0.25) and slots[4] == pytest.approx(0.25)
    assert slots[32] == pytest.approx(0.5) and slots[44:] == [pytest.approx(0.5)] * 4
    assert sum(slots[:44]) == pytest.approx(14.0)


def test_day_slots_take_import_shape_when_import_is_the_load():
    hh = [0.0] * 48
    hh[32:38] = [0.2, 0.2, 0.2, 0.2, 1.1, 1.1]  # 3.0 kWh = the whole peak load
    slots = planning.day_load_slots(_snaps(), hh)
    assert slots[36] == pytest.approx(1.1) and slots[32] == pytest.approx(0.2)
    # Battery covering the evening: import far below load → even spread.
    hh2 = [0.0] * 48
    hh2[38] = 0.5
    assert planning.day_load_slots(_snaps(), hh2)[38] == pytest.approx(0.5)


def test_day_slots_missing_segment_is_none():
    snaps = _snaps()
    del snaps["16:00"]
    assert planning.day_load_slots(snaps) is None


TODAY = date(2026, 11, 5)  # Thursday


def _days(n, **kw):
    return {(TODAY - timedelta(days=i + 1)).isoformat(): _snaps(**kw) for i in range(n)}


def test_profile_needs_min_days_and_summarises():
    assert planning.load_profile(_days(5), {}, TODAY, False) is None
    p = planning.load_profile(_days(10), {}, TODAY, False)
    assert p["days"] == 8 and p["day_type"] == "weekday"  # 8 weekdays in the last 10 days
    assert p["peak_kwh"] == pytest.approx(3.0)
    assert p["evening_kw"] == pytest.approx((3.0 + 2.0 + 1.0) / 7, abs=0.001)  # 19-22, 22-24 at the 19-22 rate, 00-02
    assert p["day_kw"] == pytest.approx(0.5)
    assert p["temp_scale"] is None


def test_profile_uses_matching_day_type():
    days = _days(21)
    for d in list(days):
        if date.fromisoformat(d).weekday() >= 5:
            days[d] = _snaps(peak=6.0)
    weekday = planning.load_profile(days, {}, TODAY, False)
    weekend = planning.load_profile(days, {}, TODAY, True)
    assert weekday["day_type"] == "weekday" and weekday["peak_kwh"] == pytest.approx(3.0)
    assert weekend["day_type"] == "all"  # only 6 weekend days in 21


def test_profile_skips_away_days_and_window():
    days = _days(10)
    skip = set(list(days)[:4])
    assert planning.load_profile(days, {}, TODAY, False, skip) is None
    assert planning.load_profile(days, {}, TODAY + timedelta(days=40), False) is None


def test_heating_scale():
    cold = [(20.0 + 2.0 * max(0, 15.5 - t), t) for t in range(0, 15)]
    scale = planning.heating_scale(cold, 0.0)
    assert scale > 1.0
    assert planning.heating_scale(cold, 20.0) < 1.0
    assert planning.heating_scale(cold[:5], 0.0) is None
    assert planning.heating_scale([(20.0, 10.0)] * 20, 0.0) is None  # no spread
    assert planning.heating_scale(cold, None) is None


def test_profile_applies_temperature_scale():
    days = _days(20)
    temps = {d: float(i % 10) for i, d in enumerate(days)}
    days = {d: _snaps(peak=2.0 + (10 - temps[d]) * 0.3) for d in days}
    p = planning.load_profile(days, {}, TODAY, False, temps=temps, temp_now=-2.0)
    plain = planning.load_profile(days, {}, TODAY, False)
    assert p["temp_scale"] > 1.0 and p["total_kwh"] > plain["total_kwh"]


def test_backtest_walk_forward():
    out = planning.load_profile_backtest(_days(20), {})
    assert out["days"] > 0 and out["profile_mae_kwh"] == 0.0 and out["median_mae_kwh"] == 0.0
    assert planning.load_profile_backtest({}, {}) == {"days": 0, "profile_mae_kwh": None, "median_mae_kwh": None}


def test_mpc_accepts_the_profile():
    p = planning.load_profile(_days(10), {}, TODAY, False)
    slots = planning.mpc_slots(600, {"offpeak": 10.0, "day": 30.0, "peak": 40.0}, [], {}, {}, {}, p["slots"])
    assert slots[0]["load_kwh"] == p["slots"][20]


def test_backtest_load_profile_cli(tmp_path, capsys):
    import importlib.util
    import json
    from pathlib import Path

    lines = [{"type": "meter_snapshot", "date": d, "time": t, **snap} for d, s in _days(12).items() for t, snap in s.items()]
    (tmp_path / "2026-10.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    path = Path(__file__).resolve().parent.parent / "tools" / "backtest.py"
    spec = importlib.util.spec_from_file_location("backtest_tool_profile", path)
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    report = bt.main([str(tmp_path), "--load-profile"])
    assert json.loads(capsys.readouterr().out) == report
    assert report["days"] > 0
