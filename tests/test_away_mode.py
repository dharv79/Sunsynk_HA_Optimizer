"""Tests for the home/away calibration split (v1.0.9)."""

from __future__ import annotations

from conftest import make_day


def test_drain_night_matches_regime(dl):
    home = make_day(initial_soc=40, target_soc=55, away=False)
    away = make_day(initial_soc=40, target_soc=55, away=True)
    assert dl._is_drain_night(home, away=False) is True
    assert dl._is_drain_night(home, away=True) is False
    assert dl._is_drain_night(away, away=True) is True
    assert dl._is_drain_night(away, away=False) is False


def test_drain_buffer_is_independent_per_regime(dl):
    # Home nights drain ~12%; away nights (low load) drain ~2%.
    home = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=12, away=False) for _ in range(6)]
    away = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=2, away=True) for _ in range(6)]
    days = home + away
    # Home profile isn't dragged down by the away days, and vice versa.
    assert dl.compute_overnight_drain_adjustment(days, away=False) == 10   # round(12/5)*5
    assert dl.compute_overnight_drain_adjustment(days, away=True) == 0     # round(2/5)*5


def test_away_regime_falls_back_to_lower_away_default(dl):
    # All history is home; the away profile has no samples -> the LOWER away
    # default (holidays are known-low-drain), not the home default.
    home = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=12, away=False) for _ in range(6)]
    assert dl.compute_overnight_drain_adjustment(home, away=True) == dl._DEFAULT_DRAIN_ADJUSTMENT_AWAY
    assert dl._DEFAULT_DRAIN_ADJUSTMENT_AWAY < dl._DEFAULT_DRAIN_ADJUSTMENT


def test_home_regime_still_uses_home_default(dl):
    # Thin home history -> home default (unchanged).
    away = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=2, away=True) for _ in range(6)]
    assert dl.compute_overnight_drain_adjustment(away, away=False) == dl._DEFAULT_DRAIN_ADJUSTMENT


def test_drain_counter_matches_regime(dl):
    days = [
        make_day(initial_soc=40, target_soc=55, away=False),
        make_day(initial_soc=40, target_soc=55, away=True),
        make_day(initial_soc=40, target_soc=55, away=True),
    ]
    assert dl.count_drain_adjustment_days(days, away=False) == 1
    assert dl.count_drain_adjustment_days(days, away=True) == 2


def test_soc_nudge_split_by_regime(dl):
    # Home ends too full (-5); away ends mid-band (0). Regimes must not mix.
    home = [make_day(evening_soc=40, actual_solar_kwh=5.0, away=False) for _ in range(5)]
    away = [make_day(evening_soc=27, actual_solar_kwh=5.0, away=True) for _ in range(5)]
    days = home + away
    assert dl.compute_soc_target_adjustment(days, "summer_like", away=False) == -5
    assert dl.compute_soc_target_adjustment(days, "summer_like", away=True) == 0


def test_charge_rate_ignores_away_days(dl):
    # Charge rate is physical: away days must not contribute to calibration.
    away_only = [
        make_day(initial_soc=20, target_soc=60, morning_soc=52, flux1_end="04:00", away=True)
        for _ in range(4)
    ]
    assert dl.compute_effective_charge_rate_kw(away_only, 10.0, 15) is None
    assert dl.count_charge_rate_calibration_days(away_only) == 0
