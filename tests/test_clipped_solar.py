"""Tests for phase 29: clipped solar detection."""

from __future__ import annotations


def test_is_clipping_needs_full_battery_and_export_at_limit(planning):
    assert planning.is_clipping(100, -3700, 3680)
    assert planning.is_clipping(99, -3500, 3680)  # 95% boundary
    assert not planning.is_clipping(98, -3700, 3680)
    assert not planning.is_clipping(100, -2000, 3680)
    assert not planning.is_clipping(100, 500, 3680)  # importing


def test_is_clipping_off_or_missing_inputs(planning):
    assert not planning.is_clipping(100, -3700, 0)
    assert not planning.is_clipping(100, -3700, None)
    assert not planning.is_clipping(None, -3700, 3680)
    assert not planning.is_clipping(100, None, 3680)


def test_estimate_clipped_kwh(planning):
    # Forecast 4 kWh this hour, PV 3 kW over 30 min: (4*0.5) - (3*0.5) = 0.5 kWh lost.
    assert planning.estimate_clipped_kwh(4.0, 3000) == 0.5
    # Actual above forecast → no loss, never negative.
    assert planning.estimate_clipped_kwh(2.0, 5000) == 0.0


def test_estimate_clipped_kwh_missing_is_none_not_zero(planning):
    assert planning.estimate_clipped_kwh(None, 3000) is None
    assert planning.estimate_clipped_kwh(4.0, None) is None


def test_overnight_target_too_high_flag(planning):
    assert planning.overnight_target_too_high(1.2, 9.5) is True
    assert planning.overnight_target_too_high(1.2, 12.0) is False
    assert planning.overnight_target_too_high(0.0, 9.5) is False
    assert planning.overnight_target_too_high(None, 9.5) is None
    assert planning.overnight_target_too_high(1.2, None) is None
