"""Tests for phase 139: Solcast's own low and high estimates (shadow)."""

from __future__ import annotations

import ast
from datetime import date
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


def test_daily_attributes_come_first(planning):
    band = planning.solcast_band({"estimate10": 1.234, "estimate90": 4.5}, None, DAY, LONDON, 3.0)
    assert band == {"solcast_p10_kwh": 1.23, "solcast_p90_kwh": 4.5}


def test_detailed_forecast_band_is_a_ratio_of_the_raw_forecast(planning):
    items = [
        {"period_start": "2026-11-28T10:00:00+00:00", "pv_estimate": 1.0, "pv_estimate10": 0.5, "pv_estimate90": 1.5},
        {"period_start": "2026-11-28T10:30:00+00:00", "pv_estimate": 1.0, "pv_estimate10": 0.5, "pv_estimate90": 1.5},
        {"period_start": "2026-11-29T10:00:00+00:00", "pv_estimate": 9.0, "pv_estimate10": 0.0, "pv_estimate90": 9.0},
    ]
    band = planning.solcast_band({}, items, DAY, LONDON, 4.0)
    assert band == {"solcast_p10_kwh": 2.0, "solcast_p90_kwh": 6.0}


def test_forecast_solar_has_no_band(planning):
    empty = {"solcast_p10_kwh": None, "solcast_p90_kwh": None}
    assert planning.solcast_band({"watts": {}}, {"2026-11-28 10:00:00": 1.0}, DAY, LONDON, 4.0) == empty
    items = [{"period_start": "2026-11-28T10:00:00+00:00", "pv_estimate": 1.0}]
    assert planning.solcast_band(None, items, DAY, LONDON, 4.0) == empty
    assert planning.solcast_band(None, None, DAY, LONDON, 4.0) == empty


def test_band_calibration_shares(planning):
    rows = [(1.0, 2.0, 5.0)] + [(3.0, 2.0, 5.0)] * 8 + [(6.0, 2.0, 5.0)]
    cal = planning.band_calibration(rows)
    assert cal == {"days": 10, "below_p10": 0.1, "inside": 0.8, "above_p90": 0.1, "miss": 0.0}
    assert planning.band_calibration([])["miss"] is None


def _plans(n, solcast, learned, actual=3.0):
    recs = []
    for i in range(n):
        d = f"2026-11-{i + 1:02d}"
        recs.append({"type": "import_plan", "date": d, "solcast_p10_kwh": solcast[0], "solcast_p90_kwh": solcast[1],
                     "forecast_quantiles": {"p10_kwh": learned[0], "p90_kwh": learned[1]}})
        recs.append({"type": "day_actuals", "date": d, "actual_solar_kwh": actual if i % 2 else 0.5})
    return recs


def test_scorecard_compares_solcast_with_the_learned_band(planning):
    # Learned band misses every low day; Solcast's wider band catches them.
    card = planning.shadow_scorecard(_plans(10, (0.2, 4.0), (2.0, 4.0)), "2026-11-11")["solcast_band"]
    assert card["verdict"] == "ready" and card["calibration"]["below_p10"] == 0.0
    assert card["learned_calibration"]["below_p10"] == 0.5
    assert planning.shadow_scorecard([], "2026-11-11")["solcast_band"]["verdict"] == "no_records"


def test_plan_logs_the_band_and_the_rule_is_unchanged():
    text = (PKG / "data_logger.py").read_text()
    assert '"solcast_p10_kwh"' in text and '"solcast_p90_kwh"' in text
    src = _function_source(PKG / "opt_overnight.py", "_async_shadow_forecast_fields")
    assert "_solcast_band_fields" in src
    assert "solcast" not in _function_source(PKG / "planning" / "overnight.py", "select_target_soc")
    assert 'shadows.append("solcast_band")' in (PKG / "opt_monitor.py").read_text()
