"""Tests for phase 55: evening reserve from the learned 16:00-22:00 load."""

from __future__ import annotations

from datetime import date, timedelta

TODAY = date(2026, 11, 4)  # Wednesday


def _snaps(load_16, load_22):
    return {"16:00": {"load_kwh": load_16}, "22:00": {"load_kwh": load_22}}


def _days(n, evening_kwh, start=1):
    return {
        (TODAY - timedelta(days=i)).isoformat(): _snaps(5.0, 5.0 + evening_kwh)
        for i in range(start, start + n)
    }


def test_median_rate_over_six_hours(planning):
    kw, n = planning.learned_evening_load_kw(_days(8, 9.0), TODAY)
    assert (kw, n) == (1.5, 8)


def test_below_minimum_days_is_none(planning):
    kw, n = planning.learned_evening_load_kw(_days(6, 9.0), TODAY)
    assert kw is None and n == 6


def test_missing_or_backwards_meter_skips_the_day(planning):
    days = _days(7, 9.0)
    days[(TODAY - timedelta(days=10)).isoformat()] = {"16:00": {"load_kwh": 5.0}}
    days[(TODAY - timedelta(days=11)).isoformat()] = _snaps(8.0, 2.0)
    days[(TODAY - timedelta(days=12)).isoformat()] = _snaps(5.0, 5.0)
    kw, n = planning.learned_evening_load_kw(days, TODAY)
    assert n == 7 and kw == 1.5


def test_away_days_and_old_days_are_skipped(planning):
    days = _days(8, 9.0)
    away = (TODAY - timedelta(days=3)).isoformat()
    days[away] = _snaps(5.0, 50.0)
    days[(TODAY - timedelta(days=40)).isoformat()] = _snaps(5.0, 50.0)
    kw, n = planning.learned_evening_load_kw(days, TODAY, skip_dates={away})
    assert (kw, n) == (1.5, 7)


def test_weekend_split_when_enough_matching_days(planning):
    days = {}
    for i in range(1, 29):
        d = TODAY - timedelta(days=i)
        days[d.isoformat()] = _snaps(0.0, 12.0 if d.weekday() >= 5 else 6.0)
    assert planning.learned_evening_load_kw(days, TODAY, weekend=True) == (2.0, 8)
    assert planning.learned_evening_load_kw(days, TODAY, weekend=False) == (1.0, 20)


def test_shadow_reserve_logged_but_not_in_force(planning):
    base, learned = planning.evening_reserve_choice(0.8, 1.4, 10.0, live=False)
    assert base == planning.evening_reserve_soc(0.8, 10.0)
    assert learned == planning.evening_reserve_soc(1.4, 10.0) and learned > base


def test_live_uses_learned_reserve(planning):
    used, learned = planning.evening_reserve_choice(0.8, 1.4, 10.0, live=True)
    assert used == learned


def test_learned_load_is_clamped_to_twice_plan(planning):
    _used, learned = planning.evening_reserve_choice(0.8, 9.0, 10.0, live=True)
    assert learned == planning.evening_reserve_soc(1.6, 10.0)


def test_no_learned_load_keeps_base(planning):
    used, learned = planning.evening_reserve_choice(0.8, None, 10.0, live=True)
    assert learned is None and used == planning.evening_reserve_soc(0.8, 10.0)
