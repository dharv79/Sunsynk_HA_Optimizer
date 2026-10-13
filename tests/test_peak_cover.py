"""Tests for phase 30: peak cover (reach 16:00 with enough battery for 16:00-19:00)."""

from __future__ import annotations

DAY, PEAK = 27.77, 38.88


def _plan(planning, **overrides):
    args = dict(
        soc=40.0, capacity_kwh=10.0, minutes_to_peak=180, solar_kwh=0.5, load_kw=0.8,
        peak_load_kwh=3.0, charge_rate_kw=3.0, day_pence=DAY, peak_pence=PEAK,
    )
    args.update(overrides)
    return planning.peak_cover_plan(**args)


def test_enough_battery_means_no_action(planning):
    plan = _plan(planning, soc=85.0)
    assert plan["decision"] == "ok" and plan["target_soc"] is None and not plan["push_now"]
    assert plan["topup_kwh"] == 0.0


def test_shortfall_tops_up_to_the_required_soc(planning):
    # 40% - (0.8 kW x 3 h - 0.5 kWh)/10 = 21%: it would run flat, so it sits at the 25% floor.
    plan = _plan(planning)
    assert plan["projected_soc_16"] == 25.0
    assert plan["required_soc"] == 58  # 25 + 3.0 kWh x 1.1 margin / 10 kWh
    assert plan["decision"] == "top_up" and plan["target_soc"] == 58
    assert plan["topup_kwh"] == 3.3  # (58 - 25)% of 10 kWh
    assert plan["gain_gbp"] > 0


def test_topup_waits_until_the_charge_needs_to_start(planning):
    early = _plan(planning, minutes_to_peak=240)
    assert early["decision"] == "top_up" and not early["push_now"]
    late = _plan(planning, minutes_to_peak=early["charge_minutes"] + 30)
    assert late["push_now"]


def test_solar_can_close_the_gap(planning):
    assert _plan(planning, solar_kwh=6.0)["decision"] == "ok"


def test_projection_is_capped_at_full(planning):
    assert _plan(planning, soc=95.0, solar_kwh=8.0)["projected_soc_16"] == 100.0


def test_missing_price_is_never_a_decision_to_charge(planning):
    assert _plan(planning, day_pence=None)["decision"] == "no_price"
    assert _plan(planning, peak_pence=None)["decision"] == "no_price"


def test_not_worth_it_when_day_rate_is_close_to_peak(planning):
    # 35p / 0.92 = 38p: barely below the 38.88p peak -> worth; 37p / 0.92 = 40.2p > 38.88p -> not.
    assert _plan(planning, day_pence=35.0)["decision"] == "top_up"
    assert _plan(planning, day_pence=37.0)["decision"] == "not_worth_it"
    assert planning.peak_cover_worth_it(None, 38.0) is None


def test_solar_until_sums_hour_buckets(planning):
    hourly = {12: 1.0, 13: 2.0, 14: 1.5, 15: 0.5, 16: 9.0}
    assert planning.solar_until_kwh(hourly, 12.0) == 5.0
    assert planning.solar_until_kwh(hourly, 13.5) == 3.0  # half of 13:00 + 14:00 + 15:00
    assert planning.solar_until_kwh(None, 12.0) is None
    assert planning.solar_until_kwh({}, 12.0) is None


def test_daytime_load_uses_observed_once_two_hours_exist(planning):
    assert planning.daytime_load_kw(8.0, 2.0, 6.0, 0.5) == (1.0, "observed")
    assert planning.daytime_load_kw(8.0, 2.0, 1.0, 0.5) == (0.5, "fallback")
    assert planning.daytime_load_kw(None, 2.0, 6.0, 0.5) == (0.5, "fallback")
    assert planning.daytime_load_kw(2.0, 2.0, 6.0, 0.5) == (0.5, "fallback")


def test_peak_load_estimate_covers_most_days(planning):
    assert planning.peak_load_estimate_kwh([2.0, 2.6, 3.1, 5.6, 2.1], 3.0) == (3.1, "history")
    assert planning.peak_load_estimate_kwh([2.0, 5.0], 3.0) == (3.0, "default")
    assert planning.peak_load_estimate_kwh([], 3.0) == (3.0, "default")
