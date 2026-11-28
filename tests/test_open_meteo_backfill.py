"""Tests for phase 138: backfill the Open-Meteo model from archived forecasts."""

from __future__ import annotations

import ast
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


def _archive_days(start: date, n: int, ref: float = 1.0, cloud: float = 10.0):
    """n archive days with the same reference kWh in hours 9-14."""
    return {
        (start + timedelta(days=i)).isoformat(): {
            "ref": {h: ref for h in range(9, 15)}, "cloud": {h: cloud for h in range(9, 15)},
        }
        for i in range(n)
    }


def test_statistics_rows_map_to_local_hours(planning):
    june = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)  # 11:00 BST
    rows = [
        {"start": june.timestamp(), "change": 0.5},
        {"start": (june + timedelta(hours=1)).timestamp() * 1000, "change": 0.75},  # milliseconds
        {"start": june + timedelta(hours=2), "change": 1.0},
        {"start": "2026-06-01T13:00:00+00:00", "change": 1.25},
        {"start": june + timedelta(hours=4), "change": None},
        {"start": june + timedelta(hours=5), "change": -0.2},
    ]
    assert planning.stats_hourly_kwh(rows, LONDON) == {"2026-06-01": {11: 0.5, 12: 0.75, 13: 1.0, 14: 1.25}}


def test_archive_days_split_a_response_by_local_day(planning):
    times, gti, cloud = [], [], []
    start = datetime(2026, 6, 1, 0, 0)
    for i in range(48):
        stamp = start + timedelta(hours=i)
        times.append(stamp.strftime("%Y-%m-%dT%H:%M"))
        gti.append(500.0 if 8 <= stamp.hour <= 16 else 0.0)
        cloud.append(20.0)
    days = planning.om_archive_days(times, [gti], [4.0], cloud, [date(2026, 6, 1), date(2026, 6, 2)], LONDON)
    assert set(days) == {"2026-06-01", "2026-06-02"}
    assert days["2026-06-01"]["ref"][9] == 2.0  # 500 W/m2 x 4 kWp, stamp 08:00 UTC = 09:00 BST hour ending
    assert days["2026-06-01"]["cloud"][9] == 20.0


def test_backtest_is_walk_forward(planning):
    days = _archive_days(date(2026, 5, 1), 30)
    pv = {d: {h: 0.5 for h in range(9, 15)} for d in days}
    hours = planning.om_archive_training_hours(days, pv, LAT, LON, LONDON)
    actuals = {d: 3.0 for d in days}
    result = planning.om_backtest(days, hours, actuals, {}, LAT, LON, LONDON)
    assert result["days"] == 30
    # The first day has no earlier hours, so it cannot have learned from itself.
    assert result["learned_days"] < 30
    first = min(days)
    alone = planning.om_backtest({first: days[first]}, hours, actuals, {}, LAT, LON, LONDON)
    assert alone["learned_days"] == 0
    # Once learned, the 0.5 ratio is found: later days are near-perfect.
    late = {d: days[d] for d in sorted(days)[-5:]}
    tail = planning.om_backtest(late, hours, actuals, {}, LAT, LON, LONDON)
    assert tail["learned_days"] == 5 and tail["om_mae"] < 0.05


def test_backtest_pairs_only_days_with_a_live_forecast(planning):
    days = _archive_days(date(2026, 5, 1), 20)
    pv = {d: {h: 0.5 for h in range(9, 15)} for d in days}
    hours = planning.om_archive_training_hours(days, pv, LAT, LON, LONDON)
    later = sorted(days)[-3:]
    live = {d: 5.0 for d in later}
    result = planning.om_backtest(days, hours, {d: 3.0 for d in days}, live, LAT, LON, LONDON)
    assert result["paired_days"] == 3 and result["live_mae"] == 2.0
    assert result["om_paired_mae"] < result["live_mae"]


def test_daily_only_fallback_scores_without_hourly_solar(planning):
    days = _archive_days(date(2026, 5, 1), 5)
    result = planning.om_backtest(days, [], {d: 3.0 for d in days}, {}, LAT, LON, LONDON)
    assert result["days"] == 5 and result["learned_days"] == 0  # default ratio, nothing trained


def test_days_without_an_actual_are_skipped(planning):
    days = _archive_days(date(2026, 5, 1), 4)
    result = planning.om_backtest(days, [], {}, {}, LAT, LON, LONDON)
    assert result["days"] == 0 and result["om_mae"] is None


def test_split_backfill_prefers_live_days(planning):
    today = date(2026, 11, 28)
    hours = [
        {"date": "2026-11-20", "x": 1},  # live day: dropped
        {"date": "2026-10-01", "x": 2},  # in the window
        {"date": "2026-03-01", "x": 3},  # older: shading only
        {"date": "2026-11-28", "x": 4},  # today: dropped
    ]
    window, older = planning.om_split_backfill(hours, {"2026-11-20"}, today)
    assert [h["x"] for h in window] == [2] and [h["x"] for h in older] == [3]


def test_older_hours_add_shading_only(planning):
    def hour(d, bin_, pv):
        return {"date": d, "hour": 12, "ref_kwh": 1.0, "pv_kwh": pv, "cloud_pct": 10.0, "bin": bin_}

    recent = [hour(f"2026-11-{i:02d}", "e15a180", 0.8) for i in range(1, 11)]
    older = [hour(f"2026-06-{i:02d}", "e60a180", 0.4) for i in range(1, 11)]
    base = planning.om_learn(recent)
    both = planning.om_learn(recent, older)
    assert "e60a180" not in base["shade"] and "e60a180" in both["shade"]
    assert both["days"] == base["days"] and both["hours"] == base["hours"]


def test_scorecard_shows_the_backtest_apart(planning):
    records = [{
        "type": "om_backfill", "status": "ok", "date": "2026-11-28", "recorded_at": "2026-11-28T03:00:00+00:00",
        "days": 200, "paired_days": 150, "om_err_sum": 150.0, "live_err_sum": 300.0, "archive": "previous_runs",
    }]
    card = planning.shadow_scorecard(records, "2026-11-28")
    row = card["open_meteo_backtest"]
    assert row["verdict"] == "ready" and row["error_ratio"] == 0.5
    assert card["open_meteo"]["verdict"] == "no_records"


def test_archive_fetch_rounds_the_location_and_uses_the_day_ahead_runs():
    src = _function_source(PKG / "open_meteo.py", "async_fetch_tilted_archive")
    assert "round(float(lat), 2)" in src and "round(float(lon), 2)" in src
    text = (PKG / "open_meteo.py").read_text()
    assert "_previous_day1" in text and "previous-runs-api.open-meteo.com" in text


def test_backfill_runs_once_at_startup_and_on_the_button():
    assert "_async_om_backfill_startup" in _function_source(PKG / "optimizer.py", "async_setup")
    assert "backfill_solar_history" in (PKG / "button.py").read_text()
    run = _function_source(PKG / "opt_shadow.py", "async_run_om_backfill")
    assert "om_backfilled" in run and "_async_write_setting" not in run and "_async_post_with_status" not in run


def test_live_forecast_learns_from_the_backfill():
    src = _function_source(PKG / "opt_shadow.py", "_async_open_meteo_shadow")
    assert "async_load_om_backfill" in src and "om_split_backfill" in src
