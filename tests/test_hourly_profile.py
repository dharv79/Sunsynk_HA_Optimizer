"""Phase 86: hourly profile and reserve-hold review (shadow)."""

from __future__ import annotations

from conftest import _data_logger, _planning as planning


def _s(h, m, soc, imp, load):
    return {"minute": h * 60 + m, "soc": soc, "grid_import_kwh": imp, "load_kwh": load}


def test_steps_belong_to_the_earlier_samples_hour():
    rows = planning.hourly_profile([_s(9, 10, 60, 1.0, 5.0), _s(9, 40, 55, 1.4, 5.5), _s(10, 10, 50, 2.0, 6.2)])
    by_hour = {r["hour"]: r for r in rows}
    assert by_hour[9]["grid_import_kwh"] == 1.0 and by_hour[9]["load_kwh"] == 1.2 and by_hour[9]["soc"] == 60
    assert by_hour[10]["grid_import_kwh"] is None  # last sample has no step: None, never 0


def test_counter_reset_and_missing_contribute_nothing():
    rows = planning.hourly_profile([_s(9, 0, 60, 3.0, 5.0), _s(9, 30, 55, 0.2, None), _s(10, 0, 50, 0.5, 6.0)])
    row = rows[0]
    assert row["grid_import_kwh"] == 0.3 and row["load_kwh"] is None
    assert planning.hourly_profile([]) == []


def test_reserve_hold_split():
    hourly = [
        {"hour": 8, "soc": 60, "grid_import_kwh": 0.8},   # day, charge in hand
        {"hour": 12, "soc": 26, "grid_import_kwh": 1.5},  # day, at the floor
        {"hour": 17, "soc": 50, "grid_import_kwh": 0.4},  # peak
        {"hour": 3, "soc": 30, "grid_import_kwh": 3.0},   # night charge is not counted
        {"hour": 9, "soc": None, "grid_import_kwh": 2.0}, # unknown soc skipped
        {"hour": 10, "soc": 70, "grid_import_kwh": 0.0},
    ]
    out = planning.reserve_hold_cost(hourly, 25.0, {"day": 25.78})
    assert out["above_floor_day_import_kwh"] == 0.8 and out["at_floor_day_import_kwh"] == 1.5
    assert out["peak_import_kwh"] == 0.4 and out["above_floor_day_import_gbp"] == round(0.8 * 25.78 / 100, 2)


def test_reserve_hold_none_when_nothing_to_split():
    out = planning.reserve_hold_cost([], 25.0, {"day": None})
    assert all(v is None for v in out.values())
    assert planning.reserve_hold_cost([{"hour": 8, "soc": 60, "grid_import_kwh": 1.0}], 25.0, {})["above_floor_day_import_gbp"] is None


def test_scorecard_entry_and_dedup():
    recs = [{"type": "hourly_profile", "date": f"2026-11-0{i}", "above_floor_day_import_kwh": 1.0 if i < 4 else 0.0}
            for i in range(1, 9)]
    card = planning.shadow_scorecard(recs, "2026-11-09")["reserve_hold"]
    assert card["days"] == 8 and card["changed"] == 3 and card["delta_gbp"] is None
    assert planning.shadow_scorecard([], "2026-11-09")["reserve_hold"]["days"] == 0
    assert "hourly_profile" in _data_logger.DataLogger._DEDUP_TYPES
