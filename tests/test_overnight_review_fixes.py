"""Phase 126: overnight-plan fixes from the 21/11/2026 code review that aren't HA-free testable
as pure planning logic — pinned here by inspecting the optimizer's own source (same technique as
test_code_integrity.py and test_monitor_guards.py)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
OPTIMIZER_FILES = [PKG / "optimizer.py", *sorted(PKG.glob("opt_*.py"))]


def _source(name: str) -> str:
    for f in OPTIMIZER_FILES:
        for fn in ast.walk(ast.parse(f.read_text())):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return ast.unparse(fn)
    raise AssertionError(f"{name} not found")


def test_charge_watchdog_skips_during_an_outage():
    for name in ("async_run_charge_watchdog", "async_run_charge_watchdog_recheck"):
        src = _source(name)
        assert "_outage_active" in src and "grid_outage" in src, name


def test_plan_retry_marks_plan_skipped_on_outage():
    src = _source("async_run_plan_retry")
    assert src.index("_outage_active") < src.index("plan_skipped")


def test_forecast_fallback_does_not_chain():
    src = _source("_resolve_raw_forecast")
    assert "forecast_fallback" in src


def test_01_55_listener_skips_the_autumn_duplicate():
    src = _source("_async_run_import_plan")
    assert "nightly_import_plan" in src and "'automatic'" in src.replace('"', "'")


def test_02_00_runs_the_dst_catch_up():
    assert "_async_nightly_plan_catch_up" in _source("_async_meter_snapshot")
    src = _source("_async_nightly_plan_catch_up")
    assert "nightly_plan_catch_up_due" in src and "_slots_held" in src and "_outage_active" in src


def test_hourly_forecast_is_kept_to_today():
    src = _source("_get_hourly_forecast_kwh")
    assert "parse_naive_hourly_forecast" in src
    assert "local.date().isoformat()!=today" in src.replace(" ", "")


def test_weekday_scores_use_local_time_and_first_occurrence_only():
    src = _source("_async_weekday_weather_scores")
    assert "as_local" in src
    assert "scores[day_name] is None" in src


def test_full_charge_day_keeps_the_current_pick_with_no_weather_data():
    src = _source("async_choose_best_full_charge_day")
    assert "all(" in src and "selected_full_charge_day" in src
