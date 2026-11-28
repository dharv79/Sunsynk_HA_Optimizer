"""Tests for phase 140: forecast confidence from the Open-Meteo ensemble (shadow)."""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
LONDON = ZoneInfo("Europe/London")
DAY = date(2026, 11, 28)


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def _times():
    start = datetime(2026, 11, 28, 0, 0)
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(24)]


def _run(scale: float):
    return [scale * 100.0 if 9 <= i <= 15 else 0.0 for i in range(24)]


def test_band_scales_the_learned_forecast_by_the_spread(planning):
    runs = [_run(0.5 + i * 0.05) for i in range(21)]  # 0.5 .. 1.5, mean 1.0
    band = planning.om_ensemble_band(_times(), [runs], [4.0], DAY, LONDON, 3.0)
    assert band["om_members"] == 21
    assert band["om_p50_kwh"] == 3.0
    assert band["om_p10_kwh"] == 1.8 and band["om_p90_kwh"] == 4.2
    assert band["om_spread"] == 80.0


def test_identical_runs_have_no_spread(planning):
    band = planning.om_ensemble_band(_times(), [[_run(1.0)] * 12], [4.0], DAY, LONDON, 2.5)
    assert band["om_p10_kwh"] == band["om_p90_kwh"] == 2.5 and band["om_spread"] == 0.0


def test_too_few_runs_or_no_forecast_is_none(planning):
    few = planning.om_ensemble_band(_times(), [[_run(1.0)] * 3], [4.0], DAY, LONDON, 3.0)
    assert few["om_p10_kwh"] is None and few["om_members"] == 3
    assert planning.om_ensemble_band(_times(), [[_run(1.0)] * 20], [4.0], DAY, LONDON, None)["om_p10_kwh"] is None
    assert planning.om_ensemble_band([], [], [4.0], DAY, LONDON, 3.0)["om_members"] is None


def test_two_strings_pair_runs_by_index(planning):
    a = [_run(0.5 + i * 0.05) for i in range(21)]
    b = [_run(0.5 + i * 0.05) for i in range(21)]
    band = planning.om_ensemble_band(_times(), [a, b], [2.0, 2.0], DAY, LONDON, 3.0)
    assert band["om_p10_kwh"] == 1.8 and band["om_members"] == 21


def test_scorecard_row_and_health(planning):
    recs = []
    for i in range(8):
        d = f"2026-11-{i + 1:02d}"
        recs.append({"type": "import_plan", "date": d, "om_p10_kwh": 1.0, "om_p90_kwh": 5.0,
                     "forecast_quantiles": {"p10_kwh": 2.5, "p90_kwh": 3.5}})
        recs.append({"type": "day_actuals", "date": d, "actual_solar_kwh": 2.0 if i % 2 else 4.0})
    row = planning.shadow_scorecard(recs, "2026-11-09")["om_ensemble"]
    assert row["verdict"] == "ready" and row["learned_calibration"]["inside"] == 0.0
    assert 'shadows.append("om_ensemble")' in (PKG / "opt_monitor.py").read_text()


def test_fetch_rounds_the_location_and_the_plan_logs_the_band():
    src = _function_source(PKG / "open_meteo.py", "async_fetch_ensemble")
    assert "round(float(lat), 2)" in src and "round(float(lon), 2)" in src and "ensemble-api.open-meteo.com" in (PKG / "open_meteo.py").read_text()
    assert "_async_om_ensemble" in _function_source(PKG / "opt_shadow.py", "_async_open_meteo_shadow")
    text = (PKG / "data_logger.py").read_text()
    assert all(f'"{k}"' in text for k in ("om_p10_kwh", "om_p50_kwh", "om_p90_kwh", "om_spread", "om_members"))
