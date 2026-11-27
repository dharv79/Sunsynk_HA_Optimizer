"""Tests for phase 136: the Open-Meteo solar forecast with a learned sun and shading model (shadow)."""

from __future__ import annotations

import ast
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
LONDON = ZoneInfo("Europe/London")
LAT, LON = 51.5, -0.13


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def test_compass_direction_converts_to_open_meteo(planning):
    assert planning.compass_to_open_meteo(180) == 0
    assert planning.compass_to_open_meteo(90) == -90
    assert planning.compass_to_open_meteo(270) == 90


def test_strings_need_kwp_tilt_and_direction(planning):
    assert planning.om_strings(4.0, 35, 180) == [(35.0, 180.0, 4.0)]
    assert planning.om_strings(None, 35, 180) == []
    assert planning.om_strings(4.0, None, 180) == []
    assert planning.om_strings(4.0, 35, 90, 35, 270, 25) == [(35.0, 90.0, 3.0), (35.0, 270.0, 1.0)]
    assert planning.om_strings(4.0, 35, 90, 35, None, 25) == [(35.0, 90.0, 4.0)]  # half a string 2 = ignored


def test_sun_position_is_plausible(planning):
    el, az = planning.sun_position(LAT, LON, datetime(2026, 6, 21, 12, tzinfo=timezone.utc))
    assert 60 < el < 64 and 175 < az < 185
    el, az = planning.sun_position(LAT, LON, datetime(2026, 12, 21, 12, tzinfo=timezone.utc))
    assert 13 < el < 17
    el, az = planning.sun_position(LAT, LON, datetime(2026, 6, 21, 6, tzinfo=timezone.utc))
    assert az < 90  # morning sun is in the east
    assert planning.sun_bin(14.9, 181.0) == "180/10"


def test_reference_hours_belong_to_the_hour_before_the_stamp(planning):
    # Summer: local = UTC+1. The value stamped 12:00 UTC covers 11:00-12:00 UTC = 12:00-13:00 local.
    times = ["2026-06-21T11:00", "2026-06-21T12:00", "2026-06-21T13:00"]
    ref = planning.om_reference_hours(times, [[500, 800, None]], [4.0], date(2026, 6, 21), LONDON)
    assert ref == {11: 2.0, 12: 3.2}  # the missing value is left out, never read as 0
    two = planning.om_reference_hours(times[:2], [[500, 800], [100, 200]], [3.0, 1.0], date(2026, 6, 21), LONDON)
    assert two[12] == round(0.8 * 3 + 0.2 * 1, 4)


def test_cloud_hours_average_the_hour_ends(planning):
    times = ["2026-11-27T09:00", "2026-11-27T10:00", "2026-11-27T11:00"]
    assert planning.om_cloud_hours(times, [20, 60, 100], date(2026, 11, 27), LONDON) == {9: 40.0, 10: 80.0, 11: 100.0}


def test_hourly_counter_interpolates_to_hour_boundaries(planning):
    samples = [{"minute": 9 * 60 + 30, "pv_kwh": 1.0}, {"minute": 10 * 60 + 30, "pv_kwh": 2.0},
               {"minute": 11 * 60 + 30, "pv_kwh": 4.0}]
    assert planning.hourly_counter_kwh(samples, "pv_kwh") == {10: 1.5}
    gap = [{"minute": 8 * 60, "pv_kwh": 0.0}, {"minute": 11 * 60, "pv_kwh": 3.0}]
    assert planning.hourly_counter_kwh(gap, "pv_kwh") == {}  # 3 h apart: not trusted
    reset = [{"minute": 9 * 60, "pv_kwh": 5.0}, {"minute": 10 * 60, "pv_kwh": 0.1}]
    assert planning.hourly_counter_kwh(reset, "pv_kwh") == {}


def test_hourly_profile_carries_pv(planning):
    rows = planning.hourly_profile([
        {"minute": 600, "soc": 50, "grid_import_kwh": 1, "load_kwh": 2, "pv_kwh": 1.0},
        {"minute": 660, "soc": 52, "grid_import_kwh": 1, "load_kwh": 2.5, "pv_kwh": 1.8},
    ])
    assert rows[0]["pv_kwh"] == 0.8 and rows[1]["pv_kwh"] is None


def _history(days, shaded_hour=None, ratio=0.75, cloud=10):
    """om_forecast + hourly_profile records where actual = ratio x reference (half at shaded_hour)."""
    forecasts, profiles = [], []
    for i in range(days):
        d = (date(2026, 11, 27) - timedelta(days=i + 1)).isoformat()
        hours, rows = [], []
        for h in range(9, 15):
            ref = 1.0
            pv = ref * ratio * (0.5 if h == shaded_hour else 1.0)
            hours.append({"hour": h, "ref_kwh": ref, "cloud_pct": cloud})
            rows.append({"hour": h, "pv_kwh": pv})
        forecasts.append({"type": "om_forecast", "date": d, "hours": hours, "recorded_at": d})
        profiles.append({"type": "hourly_profile", "date": d, "hourly": rows})
    return forecasts, profiles


def test_training_joins_forecast_and_measured_hours(planning):
    forecasts, profiles = _history(3)
    profiles[0]["hourly"][0]["pv_kwh"] = None
    hours = planning.om_training_hours(forecasts, profiles, LAT, LON, LONDON, date(2026, 11, 27))
    assert len(hours) == 3 * 6 - 1
    assert all(h["date"] < "2026-11-27" for h in hours)


def test_learning_finds_the_shaded_sun_position(planning):
    forecasts, profiles = _history(10, shaded_hour=9)
    hours = planning.om_training_hours(forecasts, profiles, LAT, LON, LONDON, date(2026, 11, 27))
    model = planning.om_learn(hours)
    nine = {h["bin"] for h in hours if h["hour"] == 9}
    assert all(model["shade"][b] < 0.7 for b in nine)
    assert 0.65 < model["ratio_all"] < 0.85 and "clear" in model["ratios"] and model["days"] == 10


def test_predict_defaults_then_learns(planning):
    day = date(2026, 11, 27)
    ref = {9: 0.5, 12: 2.0, 20: 0.0}
    default = planning.om_predict(ref, {}, None, LAT, LON, day, LONDON)
    assert default["basis"] == "default" and default["hourly"][12] == round(2.0 * planning.OM_DEFAULT_RATIO, 3)
    assert default["hourly"][20] == 0.0  # sun below the horizon
    forecasts, profiles = _history(10, ratio=0.5)
    model = planning.om_learn(planning.om_training_hours(forecasts, profiles, LAT, LON, LONDON, day))
    learned = planning.om_predict(ref, {12: 10}, model, LAT, LON, day, LONDON)
    assert learned["basis"] == "learned" and abs(learned["hourly"][12] - 1.0) < 0.05


def test_solar_start_interpolates_the_crossing(planning):
    # Hour values sit at their mid-points (09:30, 10:30): 0.5 is halfway between 0.3 and 0.7.
    assert planning.om_solar_start({8: 0.1, 9: 0.3, 10: 0.7}, 0.5) == "10:00"
    assert planning.om_solar_start({9: 0.3, 10: 0.8}, 0.4) == "09:42"
    assert planning.om_solar_start({10: 0.6}, 0.5) == "10:20"  # ramps up from 0 at 09:30
    assert planning.om_solar_start({9: 0.2}, 0.5) is None
    assert planning.om_solar_start({9: 2.0}, None) is None


def test_daylight_cloud_cover_from_hourly_forecast(planning):
    items = [
        {"datetime": "2026-11-27T07:00:00+00:00", "cloud_coverage": 0},
        {"datetime": "2026-11-27T10:00:00+00:00", "cloud_coverage": 80},
        {"datetime": "2026-11-27T12:00:00+00:00", "cloud_coverage": 40},
        {"datetime": "2026-11-28T10:00:00+00:00", "cloud_coverage": 100},
    ]
    assert planning.daylight_cloud_cover(items, date(2026, 11, 27), LONDON, 8, 16) == 60.0
    assert planning.daylight_cloud_cover([], date(2026, 11, 27), LONDON, 8, 16) is None


def test_scorecard_scores_open_meteo_against_the_live_forecast(planning):
    records = []
    for i in range(10):
        d = (date(2026, 11, 27) - timedelta(days=i + 1)).isoformat()
        records.append({"type": "import_plan", "date": d, "solar_forecast_kwh": 4.0, "om_forecast_kwh": 2.2})
        records.append({"type": "day_actuals", "date": d, "actual_solar_kwh": 2.0})
    row = planning.shadow_scorecard(records, "2026-11-27")["open_meteo"]
    assert row["verdict"] == "ready" and row["error_ratio"] == 0.1


def test_plan_logs_the_forecast_and_never_lets_it_stop_the_plan():
    src = _function_source(PKG / "opt_overnight.py", "async_run_import_plan")
    assert "await self._async_open_meteo_shadow(now, avg_consumption_kw)" in src
    assert "except Exception" in src and "**om_fields" in src
    log_at = src.index("async_log_om_forecast")
    assert src.index("if is_tonights_plan:") < log_at < src.index("if source != STARTUP_PLAN_SOURCE")
    fields = (PKG / "data_logger.py").read_text()
    for key in ("om_forecast_kwh", "om_basis", "om_solar_start", "om_days", "om_cloud_pct"):
        assert f'"{key}"' in fields


def test_shadow_is_off_without_panel_config_and_rounds_the_location():
    src = _function_source(PKG / "opt_shadow.py", "_async_open_meteo_shadow")
    assert "if not strings or lat is None or lon is None:" in src and "return ({}, None)" in src
    client = (PKG / "open_meteo.py").read_text()
    assert "round(float(lat), 2)" in client and "round(float(lon), 2)" in client


def test_cloud_cover_falls_back_to_the_hourly_forecast():
    src = _function_source(PKG / "opt_overnight.py", "_async_today_cloud_cover")
    assert "'hourly'" in src and "daylight_cloud_cover(" in src


def test_options_and_strings():
    flow = (PKG / "config_flow.py").read_text()
    for key in ("CONF_PV_TILT", "CONF_PV_AZIMUTH", "CONF_PV2_TILT", "CONF_PV2_AZIMUTH", "CONF_PV2_SHARE_PCT"):
        assert key in flow
    for name in ("strings.json", "translations/en.json"):
        text = json.loads((PKG / name).read_text())
        assert json.dumps(text).count('"pv_tilt"') == 2
