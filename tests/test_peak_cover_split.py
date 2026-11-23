"""Tests for phase 132: generous peak-cover holds, lean top-ups (typical evening + fixed buffer)."""

from __future__ import annotations

DAY, PEAK = 27.77, 38.88


def _split(planning, **overrides):
    args = dict(
        soc=40.0, capacity_kwh=10.0, minutes_to_peak=180, solar_kwh=0.5, load_kw=0.8,
        peak_load_kwh=5.9, typical_load_kwh=3.0, charge_rate_kw=3.0, day_pence=DAY, peak_pence=PEAK,
    )
    args.update(overrides)
    return planning.peak_cover_plan_split(**args)


def test_buffer_adds_fixed_kwh_before_discharge_losses(planning):
    assert round(planning.peak_need_soc(3.0, 10.0, 1.0, buffer_kwh=1.5), 2) == 48.91  # 4.5 / 0.92 / 10
    assert planning.peak_need_soc(3.0, 10.0) == planning.peak_need_soc(3.0, 10.0, buffer_kwh=0.0)


def test_hold_keeps_the_cautious_requirement(planning):
    # 23/11/2026 12:27: SOC 97% heading to ~82% by 16:00; the cautious estimate asks for 96%.
    plan = _split(planning, soc=97.0, minutes_to_peak=213, solar_kwh=0.1, load_kw=0.45)
    assert plan["decision"] == "hold" and plan["required_basis"] == "hold"
    assert plan["required_soc"] == plan["required_soc_hold"] == plan["target_soc"] == 96


def test_topup_buys_only_the_typical_evening_plus_buffer(planning):
    plan = _split(planning)
    assert plan["required_soc_hold"] == 96
    # 25% floor + (3.0 + 1.5 kWh) / 0.92 / 10 kWh = 73.9 -> 74
    assert plan["decision"] == "top_up" and plan["required_basis"] == "typical"
    assert plan["required_soc"] == plan["target_soc"] == 74
    assert plan["topup_kwh"] == 4.9


def test_between_the_two_requirements_holds_at_the_lean_one(planning):
    # SOC 80% is below the cautious 96% (no free hold there) but above the lean 74%.
    plan = _split(planning, soc=80.0)
    assert plan["decision"] == "hold" and plan["required_basis"] == "typical" and plan["target_soc"] == 74


def test_ok_needs_no_replan(planning):
    plan = _split(planning, soc=85.0, peak_load_kwh=2.0)
    assert plan["decision"] == "ok" and plan["required_basis"] == "hold" and plan["target_soc"] is None


def test_no_typical_estimate_keeps_the_cautious_plan(planning):
    plan = _split(planning, typical_load_kwh=None)
    assert plan["decision"] == "top_up" and plan["required_basis"] == "hold" and plan["target_soc"] == 96


def test_peak_minimum_soc_still_floors_the_topup(planning):
    plan = _split(planning, min_soc=90)
    assert plan["required_basis"] == "typical" and plan["target_soc"] == 90


def test_missing_price_is_no_price_not_a_topup(planning):
    plan = _split(planning, day_pence=None)
    assert plan["decision"] == "no_price" and plan["target_soc"] is None


def test_typical_is_the_recent_median(planning):
    days = [("2026-11-%02d" % d, 3.0) for d in range(15, 21)] + [("2026-11-22", 7.1)]
    assert planning.peak_load_typical(days, 2.0) == (3.0, "recent_median")  # one heavy evening barely moves it
    old = [("2026-10-%02d" % d, 9.0) for d in range(1, 8)]
    assert planning.peak_load_typical(old + days, 2.0) == (3.0, "recent_median")  # only the newest 7 count


def test_typical_needs_history(planning):
    assert planning.peak_load_typical([("2026-11-20", 3.0), ("2026-11-21", 4.0)], 2.4) == (2.4, "default")
    assert planning.peak_load_typical([("bad", 9.0)] * 5, 2.4) == (2.4, "default")
    assert planning.peak_load_typical([("2026-11-2%d" % d, v) for d, v in ((0, 2.0), (1, -1.0), (2, 4.0), (3, 3.0))],
                                      2.4) == (3.0, "recent_median")
