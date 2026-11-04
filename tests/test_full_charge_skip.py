"""Phase 68: skip the forced weekly 100% when a recent day already reached it."""
from datetime import date

TODAY = date(2026, 11, 4)


def _day(soc):
    return {"22:00": {"soc": soc}}


def test_days_since_full_uses_max_soc(planning):
    days = {"2026-11-01": _day(99.4), "2026-11-02": _day(80), "2026-10-20": _day(100)}
    assert planning.days_since_full(days, TODAY) == 3


def test_no_history_or_unreadable_soc_is_none(planning):
    assert planning.days_since_full({}, TODAY) is None
    assert planning.days_since_full({"2026-11-03": {"22:00": {"soc": None}}}, TODAY) is None


def test_recent_full_skips(planning):
    assert planning.full_charge_due(3) == (False, "recent_full_skip")


def test_gap_reached_or_unknown_is_due(planning):
    assert planning.full_charge_due(7) == (True, "gap_reached")
    assert planning.full_charge_due(None) == (True, "no_history")


def test_hard_max_gap_is_14(planning):
    assert planning.full_charge_due(15, max_gap_days=30)[0] is True
    assert planning.full_charge_due(13, max_gap_days=30)[0] is False
