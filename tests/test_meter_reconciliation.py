"""Tests for phase 44: inverter vs smart-meter reconciliation."""

from __future__ import annotations


def _slots(per_slot):
    return [per_slot] * 48


def test_matching_totals_not_diverged(planning):
    r = planning.meter_reconciliation(8.8, _slots(0.2))  # 44 slots * 0.2 = 8.8
    assert r["diff_kwh"] == 0.0 and r["diverged"] is False


def test_large_gap_diverges(planning):
    r = planning.meter_reconciliation(10.5, _slots(0.2))
    assert r["diverged"] is True and r["pct"] > 8


def test_small_pct_or_small_abs_gap_does_not_diverge(planning):
    assert planning.meter_reconciliation(9.2, _slots(0.2))["diverged"] is False  # 4.5%
    assert planning.meter_reconciliation(0.4, _slots(0.0))["diverged"] is False  # < 0.5 kWh abs


def test_missing_or_incomplete_inputs_are_none(planning):
    assert planning.meter_reconciliation(None, _slots(0.2)) is None
    assert planning.meter_reconciliation(5.0, [0.1] * 47) is None
    assert planning.meter_reconciliation(5.0, [0.1] * 47 + [None]) is None
    assert planning.meter_reconciliation(5.0, None) is None


def test_streak_counts_consecutive_trailing_days(planning):
    h = [
        {"date": "2026-10-28", "diverged": True},
        {"date": "2026-10-29", "diverged": True},
        {"date": "2026-10-30", "diverged": True},
    ]
    assert planning.divergence_streak(h) == 3
    h.append({"date": "2026-10-31", "diverged": False})
    assert planning.divergence_streak(h) == 0


def test_streak_broken_by_date_gap(planning):
    h = [{"date": "2026-10-27", "diverged": True}, {"date": "2026-10-29", "diverged": True}]
    assert planning.divergence_streak(h) == 1
    assert planning.divergence_streak([]) == 0
