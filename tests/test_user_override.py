"""Phase 46: one-night user overrides."""
from datetime import datetime


def test_next_plan_date(planning):
    assert planning.next_plan_date(datetime(2026, 11, 4, 1, 54)).isoformat() == "2026-11-04"
    assert planning.next_plan_date(datetime(2026, 11, 4, 1, 55)).isoformat() == "2026-11-05"
    assert planning.next_plan_date(datetime(2026, 11, 4, 18, 0)).isoformat() == "2026-11-05"


def test_set_overrides_target_and_clamps(planning):
    ov = {"date": "2026-11-05", "soc": 100}
    assert planning.apply_user_override(60, ov, "2026-11-05", 30) == (100, "set")
    assert planning.apply_user_override(60, {**ov, "soc": 5}, "2026-11-05", 30) == (20, "set")
    assert planning.apply_user_override(60, {**ov, "soc": 150}, "2026-11-05", 30) == (100, "set")


def test_skip_holds_battery(planning):
    ov = {"date": "2026-11-05", "skip": True}
    assert planning.apply_user_override(80, ov, "2026-11-05", 47.6) == (47, "skip")
    assert planning.apply_user_override(40, ov, "2026-11-05", 90) == (40, "skip")  # never raises the target
    assert planning.apply_user_override(80, ov, "2026-11-05", 10) == (20, "skip")  # floor
    assert planning.apply_user_override(80, ov, "2026-11-05", None) == (80, None)


def test_stale_or_missing_override_ignored(planning):
    assert planning.apply_user_override(60, {"date": "2026-11-04", "soc": 100}, "2026-11-05", 30) == (60, None)
    assert planning.apply_user_override(60, None, "2026-11-05", 30) == (60, None)
    assert planning.apply_user_override(60, {"date": "2026-11-05", "soc": True}, "2026-11-05", 30) == (60, None)
