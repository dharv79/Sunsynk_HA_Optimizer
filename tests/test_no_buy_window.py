"""Tests for phase 135: an earlier no-buy start on demand (e.g. a 15:00 saving session)."""

from __future__ import annotations

import ast
import json
from datetime import date, datetime
from pathlib import Path

import conftest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
TODAY = date(2026, 11, 27)


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def test_start_applies_on_its_own_day_only(planning):
    assert planning.no_buy_start_minutes(datetime(2026, 11, 27, 15, 0), TODAY) == 15 * 60
    assert planning.no_buy_start_minutes(datetime(2026, 11, 27, 14, 30), TODAY) == 14 * 60 + 30
    assert planning.no_buy_start_minutes(datetime(2026, 11, 26, 15, 0), TODAY) == 16 * 60
    assert planning.no_buy_start_minutes(datetime(2026, 11, 28, 15, 0), TODAY) == 16 * 60
    assert planning.no_buy_start_minutes(None, TODAY) == 16 * 60


def test_start_never_moves_later_than_16(planning):
    assert planning.no_buy_start_minutes(datetime(2026, 11, 27, 17, 0), TODAY) == 16 * 60


def test_extra_load_covers_the_hours_before_16(planning):
    assert planning.no_buy_extra_load_kwh(15 * 60, 0.8) == 0.8
    assert planning.no_buy_extra_load_kwh(14 * 60 + 30, 0.6) == 0.9
    assert planning.no_buy_extra_load_kwh(16 * 60, 0.8) == 0.0
    assert planning.no_buy_extra_load_kwh(15 * 60, None) == 0.0


def test_last_log_and_retries_move_with_the_start(planning):
    assert planning.peak_cover_last_log_minutes(16 * 60) == planning.PEAK_COVER_LAST_LOG_MINUTES
    assert planning.peak_cover_last_log_minutes(15 * 60) == 14 * 60 + 30
    assert planning.peak_cover_retry_due(False, 14 * 60 + 25, 0, last_log=14 * 60 + 30)
    assert not planning.peak_cover_retry_due(False, 14 * 60 + 35, 0, last_log=14 * 60 + 30)
    assert not planning.peak_cover_startup_retry_due(15 * 60 + 10, None, False, 0, 15 * 60)
    assert planning.peak_cover_startup_retry_due(14 * 60, None, False, 0, 15 * 60)


def test_earlier_start_raises_the_requirement(planning):
    base = dict(soc=50.0, capacity_kwh=10.0, solar_kwh=0.0, load_kw=0.8, charge_rate_kw=3.0,
                day_pence=27.77, peak_pence=38.88)
    normal = planning.peak_cover_plan_split(minutes_to_peak=120, peak_load_kwh=5.9, typical_load_kwh=3.5, **base)
    extra = planning.no_buy_extra_load_kwh(15 * 60, 0.8)
    early = planning.peak_cover_plan_split(
        minutes_to_peak=60, peak_load_kwh=5.9 + extra, typical_load_kwh=3.5 + extra, **base
    )
    assert early["required_soc"] > normal["required_soc"]


def test_peak_cover_uses_the_start_for_window_slot_and_load():
    src = _function_source(PKG / "opt_daytime.py", "async_run_peak_cover")
    assert "start = self._no_buy_start(now)" in src
    assert "minutes < start" in src
    assert "'endTime': minutes_to_hhmm(start)" in src
    assert "no_buy_extra_load_kwh(start, load_kw)" in src
    assert "start - minutes" in src


def test_cover_ends_at_the_start():
    arm = _function_source(PKG / "opt_daytime.py", "_arm_no_buy_timer")
    assert "async_track_point_in_time" in arm
    end = _function_source(PKG / "opt_daytime.py", "_async_end_cover_for_no_buy")
    assert "_async_peak_cover_end()" in end and "_flux2_lock" in end
    hold = _function_source(PKG / "opt_daytime.py", "_cover_hold_slot")
    assert "self._no_buy_start(now_local)" in hold


def test_dashboard_has_the_picker():
    dashboard = conftest._dashboard._build_dashboard({
        "inverter_serial": "2411132500", "plant_id": "111111",
        "solar_forecast_sensor": "sensor.energy_production_today", "weather_entity": "weather.forecast_home",
    })
    assert '"entity": "datetime.no_buy_from"' in json.dumps(dashboard)
