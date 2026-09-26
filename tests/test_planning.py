"""Tests for the pure import-plan maths in planning.py."""

from __future__ import annotations

import pytest


def _select(planning, **overrides):
    kwargs = dict(
        is_full_day=False,
        low_solar_forecast_kwh=15.0,
        band="summer_like",
        hours_to_solar=2.0,
        avg_consumption_kw=0.5,
        battery_capacity_kwh=10.0,
    )
    kwargs.update(overrides)
    return planning.select_target_soc(**kwargs)


@pytest.mark.parametrize("kwh,band", [(10, "summer_like"), (9.99, "shoulder"), (5.01, "shoulder"), (5, "winter_like")])
def test_forecast_band_edges(planning, kwh, band):
    assert planning.forecast_band(kwh) == band


def test_bridge_soc_clamps(planning):
    assert planning.bridge_soc(0, 10) == 30
    assert planning.bridge_soc(1, 10) == 30      # 20 + 10
    assert planning.bridge_soc(2, 10) == 40
    assert planning.bridge_soc(50, 10) == 100


def test_walk_bridge_gap_stops_at_first_covering_hour(planning):
    hourly = {5: 0.0, 6: 0.2, 7: 0.6, 8: 2.0}
    gap, hour = planning.walk_bridge_gap(hourly, 0.5)
    assert hour == 7
    assert gap == pytest.approx(0.5 + 0.3)
    # Scale applies before the comparison.
    gap, hour = planning.walk_bridge_gap(hourly, 0.5, scale=3.0)
    assert hour == 6
    assert gap == pytest.approx(0.5)


def test_walk_bridge_gap_never_covered(planning):
    gap, hour = planning.walk_bridge_gap({}, 0.5)
    assert hour is None
    assert gap == pytest.approx(0.5 * 8)  # 05:00-12:00 inclusive


def test_synthetic_profile_sums_to_daily_total(planning):
    profile = planning.synthetic_hourly_profile(20.0, 6.0, 20.0)
    assert sum(profile.values()) == pytest.approx(20.0)
    assert profile[3] == 0.0 and profile[22] == 0.0
    assert max(profile, key=profile.get) in (12, 13)
    assert planning.synthetic_hourly_profile(0, 6, 20) is None
    assert planning.synthetic_hourly_profile(10, 20, 6) is None


def test_select_low_solar_override(planning):
    d = _select(planning, low_solar_forecast_kwh=6.9, band="shoulder")
    assert (d.target_soc, d.reason) == (95, "low_solar_override")
    d = _select(planning, low_solar_forecast_kwh=3, band="winter_like")
    assert (d.target_soc, d.reason) == (100, "low_solar_override_winter_like")


def test_select_full_day(planning):
    d = _select(planning, is_full_day=True)
    assert (d.target_soc, d.reason) == (30, "weekly_full_charge_day")  # 20 + 1kWh/10 → 30
    assert _select(planning, is_full_day=True, low_solar_forecast_kwh=5).target_soc == 100
    assert _select(planning, is_full_day=True, hours_to_solar=None).target_soc == 100


def test_select_hourly_bridge_wins_over_simple(planning):
    d = _select(planning, hourly_forecast_kwh={5: 0.0, 6: 0.0, 7: 1.0}, hourly_correction=1.0)
    assert d.reason == "solar_bridge_hourly"
    assert d.hourly_forecast_used and d.bridge_hour == 7
    assert d.target_soc == 30  # gap 1.0 kWh


def test_select_synthetic_ramp_only_raises(planning):
    slow = {h: 0.0 for h in range(5, 13)}
    d = _select(planning, hours_to_solar=1.0, avg_consumption_kw=1.0, synthetic_profile=slow)
    assert d.reason == "solar_bridge_ramp" and d.synthetic_ramp
    assert d.target_soc == 100  # 8 kWh gap on 10 kWh battery
    fast = {5: 5.0}
    d = _select(planning, hours_to_solar=3.0, avg_consumption_kw=1.0, synthetic_profile=fast)
    assert d.reason == "solar_bridge" and not d.synthetic_ramp
    assert d.target_soc == 50 and d.bridge_hour == 5


@pytest.mark.parametrize("band,target", [("winter_like", 95), ("summer_like", 80), ("shoulder", 85)])
def test_select_band_fallback_without_sun(planning, band, target):
    d = _select(planning, hours_to_solar=None, band=band)
    assert (d.target_soc, d.reason) == (target, band)


def test_apply_soc_adjustments_keeps_morning_floor(planning):
    assert planning.apply_soc_adjustments(40, 10, 0) == 50
    assert planning.apply_soc_adjustments(30, 15, -5) == 40   # 40 - 15 = 25, ok
    assert planning.apply_soc_adjustments(20, 10, -5) == 35   # 25 → est 15 < 25 → 25 + 10
    assert planning.apply_soc_adjustments(95, 15, 5) == 100


@pytest.mark.parametrize("temp,factor", [(20, 1.0), (15, 0.85), (10, 0.70), (5, 0.55)])
def test_temp_deration(planning, temp, factor):
    assert planning.temp_deration_factor(temp) == factor


def test_resolve_used_charge_rate(planning):
    assert planning.resolve_used_charge_rate(3.0, None, 20) == (3.0, 1.0)
    assert planning.resolve_used_charge_rate(3.0, 2.8, 20) == (3.0, 1.0)   # within 10% → nameplate
    assert planning.resolve_used_charge_rate(3.0, 2.0, 20) == (2.0, 1.0)
    assert planning.resolve_used_charge_rate(3.0, 2.0, 8) == (1.4, 0.70)


def test_flux1_end_minutes(planning):
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(0.0, 3.0)) == "02:15"
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(1.6, 3.0)) == "02:45"  # 32 min → 45
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(3.0, 3.0)) == "03:00"
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(30.0, 3.0)) == "05:00"


def test_score_full_charge_day(planning):
    sunny = {"condition": "sunny", "cloud_coverage": 10, "precipitation_probability": 0, "temperature": 20}
    assert planning.score_full_charge_day(sunny, "Monday") == 118.0
    assert planning.score_full_charge_day(sunny, "Friday") == 103.0
    rainy = {"condition": "rainy", "cloud_coverage": 90, "precipitation_probability": 80, "temperature": 4}
    assert planning.score_full_charge_day(rainy, "Tuesday") == -74.0


def test_net_cost_and_sum_field(planning):
    assert planning.net_cost_gbp(2.0, 0.5, 1.0) == 2.5
    assert planning.net_cost_gbp(2.0, None, 1.0) is None
    days = [{"x": 1.111}, {"x": None}, {}, {"x": 2.0}]
    assert planning.sum_field(days, "x") == 3.11
    assert planning.sum_field([{}], "x") is None


# --------------------------------------------------------------------------- #
# Weekly digest period                                                        #
# --------------------------------------------------------------------------- #

def test_trailing_week_is_seven_days_ending_yesterday(planning):
    from datetime import date

    # Sunday 2026-09-20 digest covers Sun 13th .. Sat 19th.
    assert planning.trailing_week(date(2026, 9, 20)) == ("2026-09-13", "2026-09-19")


def test_days_in_period_keeps_full_week_and_drops_today(planning):
    days = [{"date": f"2026-09-{d:02d}"} for d in range(12, 21)]  # 12th..20th
    kept = planning.days_in_period(days, "2026-09-13", "2026-09-19")
    assert [d["date"] for d in kept] == [f"2026-09-{d:02d}" for d in range(13, 20)]


def test_week_history_covers_oldest_plan_record(planning):
    # Oldest day's 01:55 import_plan is ~7 d 16 h before the Sunday 18:00 digest.
    assert planning.WEEK_HISTORY_DAYS * 24 > 7 * 24 + 16
