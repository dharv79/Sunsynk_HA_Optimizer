"""Phase 84: inverter low-battery floor review (read-only)."""

from __future__ import annotations

from conftest import _data_logger, _planning as planning

PRICES = {"day": 25.78, "offpeak": 15.56}


def test_floor_found_by_name_and_range():
    value, key, names = planning.battery_floor_soc({"batteryLowCap": "25", "batteryCap": "100", "other": 1})
    assert (value, key) == (25.0, "batteryLowCap") and names == ["batteryCap", "batteryLowCap"]


def test_floor_missing_lists_candidate_keys_only():
    value, key, names = planning.battery_floor_soc({"shutdownSoc": "10", "mode": "1"})
    assert value is None and key is None and names == ["shutdownSoc"]
    assert planning.battery_floor_soc({"batteryLowCap": "bad"})[0] is None
    assert planning.battery_floor_soc({"batteryLowCap": "250"})[0] is None
    assert planning.battery_floor_soc(None) == (None, None, [])


def test_saving_counts_floor_days_and_skips_away():
    days = [{"evening_soc": 25.0}] * 8 + [{"evening_soc": 60.0}] * 2 + [{"evening_soc": 25.0, "away": True}]
    out = planning.floor_saving_gbp(days, 25.0, 15, 10.0, PRICES)
    assert out["days"] == 10 and out["hit_days"] == 8
    per_day = 1.0 * (25.78 - 15.56 / planning.CHARGE_EFFICIENCY) / 100
    assert out["saving_gbp"] == round(8 * per_day, 2)


def test_saving_none_when_inputs_missing():
    days = [{"evening_soc": 25.0}]
    assert planning.floor_saving_gbp(days, None, 15, 10.0, PRICES)["saving_gbp"] is None
    assert planning.floor_saving_gbp(days, 25.0, 15, None, PRICES)["saving_gbp"] is None
    assert planning.floor_saving_gbp(days, 25.0, 15, 10.0, {"day": None, "offpeak": 15.0})["saving_gbp"] is None
    assert planning.floor_saving_gbp([], 25.0, 15, 10.0, PRICES) == {"days": 0, "hit_days": 0, "saving_gbp": 0.0}


def test_record_type_is_deduped():
    assert "battery_floor" in _data_logger.DataLogger._DEDUP_TYPES
