"""Unit tests for the pure adaptive-learning logic in data_logger.py.

These lock in the behaviour worked out by hand-modelling for b33 (median forecast
correction), b34 (drain excludes no-charge nights), and b35 (charge-rate history
seed), so future changes are checked automatically instead of re-modelled.
"""

from __future__ import annotations

import statistics

from conftest import make_day


# --------------------------------------------------------------------------- #
# _percentile                                                                  #
# --------------------------------------------------------------------------- #

def test_percentile_empty_and_single(dl):
    assert dl._percentile([], 50) == 0.0
    assert dl._percentile([7.0], 75) == 7.0


def test_percentile_matches_statistics_median(dl):
    for vals in ([1, 2, 3, 4, 5], [1, 2, 3, 4], [2.35, 1.41, 1.86, 2.03, 1.94]):
        assert dl._percentile(vals, 50) == statistics.median(vals)


def test_percentile_p75_interpolates(dl):
    # rank = 0.75 * (5-1) = 3.0 -> exactly the 4th ordered value
    assert dl._percentile([10, 20, 30, 40, 50], 75) == 40


# --------------------------------------------------------------------------- #
# compute_forecast_correction (b33: median, not mean)                          #
# --------------------------------------------------------------------------- #

def test_forecast_correction_neutral_below_threshold(dl):
    days = [make_day() for _ in range(dl._MIN_DAYS_FORECAST_CORRECTION - 1)]
    assert dl.compute_forecast_correction(days) == 1.0


def test_forecast_correction_is_median_not_mean(dl):
    # Six ordinary ratios of ~1.6 plus one wild outlier day.
    days = [make_day(solar_forecast_kwh=10.0, actual_solar_kwh=16.0) for _ in range(6)]
    days.append(make_day(solar_forecast_kwh=0.6, actual_solar_kwh=6.0))  # ratio 10
    factor = dl.compute_forecast_correction(days)
    # Median is immune to the outlier; a mean would be dragged far above 1.6.
    assert factor == 1.6


def test_forecast_correction_skips_near_zero_forecast(dl):
    days = [make_day(solar_forecast_kwh=10.0, actual_solar_kwh=15.0) for _ in range(7)]
    days += [make_day(solar_forecast_kwh=0.2, actual_solar_kwh=5.0) for _ in range(3)]
    # The <=0.5 forecast days are filtered out, so the factor stays at 1.5.
    assert dl.compute_forecast_correction(days) == 1.5


def test_forecast_correction_capped(dl):
    days = [make_day(solar_forecast_kwh=1.0, actual_solar_kwh=100.0) for _ in range(7)]
    assert dl.compute_forecast_correction(days) == 3.0  # capped at 3.0
    days = [make_day(solar_forecast_kwh=100.0, actual_solar_kwh=1.0) for _ in range(7)]
    assert dl.compute_forecast_correction(days) == 0.5  # floored at 0.5


# --------------------------------------------------------------------------- #
# _is_drain_night + compute_overnight_drain_adjustment (b34)                   #
# --------------------------------------------------------------------------- #

def test_is_drain_night_requires_real_charge(dl):
    # initial >= target => no-charge night => excluded
    assert dl._is_drain_night(make_day(initial_soc=80, target_soc=55)) is False
    assert dl._is_drain_night(make_day(initial_soc=40, target_soc=55)) is True


def test_is_drain_night_excludes_solar_contaminated_and_full_day(dl):
    assert dl._is_drain_night(make_day(morning_pv_power=250)) is False
    assert dl._is_drain_night(make_day(is_full_day=True)) is False
    assert dl._is_drain_night(make_day(overnight_drain_pct=None)) is False
    assert dl._is_drain_night(make_day(overnight_drain_pct=-3)) is False


def test_drain_adjustment_fallback_below_threshold(dl):
    days = [make_day() for _ in range(dl._MIN_DAYS_SOC_ADJUSTMENT - 1)]
    assert dl.compute_overnight_drain_adjustment(days) == dl._DEFAULT_DRAIN_ADJUSTMENT


def test_drain_adjustment_excludes_no_charge_nights(dl):
    # 6 real-charge nights at 12% drain; adding a huge no-charge night must not move it.
    real = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=12.0) for _ in range(6)]
    no_charge = make_day(initial_soc=90, target_soc=55, overnight_drain_pct=48.0)
    assert dl.compute_overnight_drain_adjustment(real + [no_charge]) == 10  # round(12/5)*5


def test_drain_adjustment_uses_p75_and_caps_at_20(dl):
    drains = [5, 8, 10, 12, 30, 30, 30]  # p75 well up the distribution
    days = [make_day(initial_soc=40, target_soc=55, overnight_drain_pct=d) for d in drains]
    assert dl.compute_overnight_drain_adjustment(days) == 20  # capped


def test_count_drain_days_matches_compute_predicate(dl):
    days = [
        make_day(initial_soc=40, target_soc=55),   # counts
        make_day(initial_soc=90, target_soc=55),   # no-charge, excluded
        make_day(morning_pv_power=300),            # solar, excluded
    ]
    assert dl.count_drain_adjustment_days(days) == 1


# --------------------------------------------------------------------------- #
# compute_soc_target_adjustment                                                #
# --------------------------------------------------------------------------- #

def test_soc_nudge_thresholds(dl):
    def band(evening):
        days = [make_day(evening_soc=evening, actual_solar_kwh=5.0) for _ in range(5)]
        return dl.compute_soc_target_adjustment(days, "summer_like")

    assert band(40) == -5   # ends too full -> charge less
    assert band(10) == 5    # ends too empty -> charge more
    assert band(27) == 0    # inside the 20-35 dead-band


def test_soc_nudge_below_threshold_is_zero(dl):
    days = [make_day(evening_soc=40, actual_solar_kwh=5.0) for _ in range(4)]
    assert dl.compute_soc_target_adjustment(days, "summer_like") == 0


# --------------------------------------------------------------------------- #
# last_known_charge_rate_kw (b35 seed)                                          #
# --------------------------------------------------------------------------- #

def test_last_known_charge_rate_picks_most_recent_non_null(dl):
    days = [
        make_day(date="2026-07-10", effective_charge_rate_kw=None),
        make_day(date="2026-07-06", effective_charge_rate_kw=1.13),
        make_day(date="2026-07-01", effective_charge_rate_kw=1.34),
    ]
    assert dl.last_known_charge_rate_kw(days) == 1.13


def test_last_known_charge_rate_none_when_all_null(dl):
    days = [make_day(effective_charge_rate_kw=None) for _ in range(3)]
    assert dl.last_known_charge_rate_kw(days) is None


# --------------------------------------------------------------------------- #
# compute_effective_charge_rate_kw                                              #
# --------------------------------------------------------------------------- #

def test_effective_charge_rate_none_below_three_days(dl):
    days = [make_day(initial_soc=20, target_soc=60, morning_soc=50, flux1_end="04:00")]
    assert dl.compute_effective_charge_rate_kw(days, 10.0, 15) is None


def test_effective_charge_rate_positive_with_enough_days(dl):
    days = [
        make_day(initial_soc=20, target_soc=60, morning_soc=52, flux1_end="04:00")
        for _ in range(4)
    ]
    rate = dl.compute_effective_charge_rate_kw(days, 10.0, 15)
    assert rate is not None and rate > 0
