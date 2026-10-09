"""Tests for phase 23: gentler charging across the 02:00-05:00 cheap window."""

from __future__ import annotations

import math


def test_current_reaches_target_by_window_end_with_margin(planning):
    # 6 kWh over 3 h at 51.2 V = 39.06 A; x1.2 margin = 46.9 → 47 A.
    assert planning.gentle_charge_current_a(6.0, 3.0, 51.2, 100.0) == 47
    # No margin: just enough current, rounded up.
    assert planning.gentle_charge_current_a(6.0, 3.0, 51.2, 100.0, margin=1.0) == math.ceil(6000 / (51.2 * 3))


def test_current_clamps_to_min_and_max(planning):
    # A tiny top-up still gets the minimum current.
    assert planning.gentle_charge_current_a(0.2, 3.0, 51.2, 100.0) == planning.GENTLE_CHARGE_MIN_CURRENT_A
    # A big charge is capped at the inverter's max.
    assert planning.gentle_charge_current_a(20.0, 3.0, 51.2, 70.0) == 70
    # Min above max → max wins.
    assert planning.gentle_charge_current_a(0.2, 3.0, 51.2, 8.0, min_current_a=10) == 8


def test_zero_need_or_bad_inputs_leave_current_alone(planning):
    rate = planning.gentle_charge_current_a
    assert rate(0.0, 3.0, 51.2, 100.0) is None
    assert rate(-1.0, 3.0, 51.2, 100.0) is None
    assert rate(5.0, 0.0, 51.2, 100.0) is None
    assert rate(5.0, 3.0, 0.0, 100.0) is None
    assert rate(5.0, 3.0, 51.2, 0.0) is None


def test_max_current_from_nameplate_rate(planning):
    assert round(planning.max_charge_current_a(3.6, 51.2), 1) == 70.3
    assert planning.max_charge_current_a(3.6, 0) == 0.0


def test_gentle_fields_are_logged_on_the_import_plan(DataLogger):
    import sys

    fields = sys.modules["sunsynk_optimizer.data_logger"]._IMPORT_PLAN_FIELDS
    assert {"gentle_current_a", "gentle_charge_applied", "saving_session_boost"} <= set(fields)
