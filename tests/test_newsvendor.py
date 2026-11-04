"""Tests for phase 64: newsvendor quantile for the hourly solar bridge (shadow)."""

from __future__ import annotations

from datetime import date, timedelta

TODAY = date(2026, 11, 4)


def _days(planning, ratios, band="mid", forecast=10.0):
    return [
        {
            "date": (TODAY - timedelta(days=i + 1)).isoformat(),
            "solar_forecast_kwh": forecast,
            "actual_solar_kwh": forecast * r,
            "forecast_band": band,
        }
        for i, r in enumerate(ratios)
    ]


def test_costs_follow_the_plan_numbers(planning):
    # day 27.77, offpeak 16.66/0.92, export 15 -> under 9.66, over 3.1 (no wear)
    under, over = planning.newsvendor_costs(27.77, 16.66, 15.0)
    assert round(under, 1) == 9.7
    assert round(over, 1) == 3.1


def test_costs_none_when_a_price_is_missing(planning):
    assert planning.newsvendor_costs(None, 16.66, 15.0) is None
    assert planning.newsvendor_costs(27.77, None, 15.0) is None
    assert planning.newsvendor_costs(27.77, 16.66, None) is None


def test_costs_none_when_importing_at_day_rate_is_not_dearer(planning):
    assert planning.newsvendor_costs(10.0, 16.66, 15.0) is None


def test_overbuy_floor_when_surplus_is_profitable(planning):
    _under, over = planning.newsvendor_costs(27.77, 5.0, 30.0)
    assert over == planning.NEWSVENDOR_MIN_OVERBUY_P


def test_quantile_is_lower_when_underbuying_is_dearer(planning):
    # CR = 9.7/(9.7+3.1) = 0.76 -> plan at the 0.24 ratio quantile (cautious solar)
    assert planning.newsvendor_quantile(9.7, 3.1) == 0.242
    # Equal costs -> the median.
    assert planning.newsvendor_quantile(5.0, 5.0) == 0.5


def test_quantile_is_bounded(planning):
    assert planning.newsvendor_quantile(50.0, 0.1) == 0.2
    assert planning.newsvendor_quantile(0.1, 50.0) == 0.8


def test_quantile_correction_none_below_min_days(planning):
    days = _days(planning, [1.0] * 19)
    assert planning.quantile_correction(days, TODAY, 0.5) == (None, 19)


def test_quantile_correction_picks_the_requested_quantile(planning):
    ratios = [0.5 + 0.05 * i for i in range(21)]  # 0.50 .. 1.50
    days = _days(planning, ratios)
    low, n = planning.quantile_correction(days, TODAY, 0.1, half_life_days=1e9)
    high, _ = planning.quantile_correction(days, TODAY, 0.9, half_life_days=1e9)
    assert n == 21
    assert low < 0.75 and high > 1.25


def test_quantile_correction_skips_missing_actuals_and_tiny_forecasts(planning):
    days = _days(planning, [1.0] * 25)
    days[0]["actual_solar_kwh"] = None
    days[1]["solar_forecast_kwh"] = 0.2
    assert planning.quantile_correction(days, TODAY, 0.5)[1] == 23


def test_quantile_correction_prefers_a_full_band(planning):
    days = _days(planning, [0.6] * 20, band="low") + _days(planning, [1.4] * 20, band="high")
    for i, d in enumerate(days[20:]):
        d["date"] = (TODAY - timedelta(days=i + 30)).isoformat()
    ratio, n = planning.quantile_correction(days, TODAY, 0.5, band="low")
    assert ratio == 0.6 and n == 20


def test_quantile_correction_is_capped(planning):
    days = _days(planning, [5.0] * 25)
    assert planning.quantile_correction(days, TODAY, 0.5)[0] == 3.0


def test_newsvendor_target_needs_hourly_and_ratio(planning):
    hourly = {h: 1.0 for h in range(8, 12)}
    assert planning.newsvendor_target(None, 1.0, 10.0, 1.0) is None
    assert planning.newsvendor_target(hourly, 1.0, 10.0, None) is None


def test_lower_ratio_means_a_higher_target(planning):
    hourly = {7: 0.4, 8: 0.8, 9: 1.4, 10: 2.0}
    cautious = planning.newsvendor_target(hourly, 1.0, 10.0, 0.6)
    bold = planning.newsvendor_target(hourly, 1.0, 10.0, 1.4)
    assert cautious > bold


def _scored(planning, ratio):
    recs = []
    for i in range(8):
        d = (date(2026, 10, 31) - timedelta(days=i)).isoformat()
        recs.append({"type": "import_plan", "date": d, "raw_forecast_kwh": 10, "forecast_correction_factor": 1.0,
                     "newsvendor_ratio": ratio, "newsvendor_under_p": 10.0, "newsvendor_over_p": 3.0})
        recs.append({"type": "day_actuals", "date": d, "actual_solar_kwh": 8})
    return planning.shadow_scorecard(recs, "2026-11-01")["newsvendor_target"]


def test_scorecard_newsvendor_ready_when_it_cuts_the_cost(planning):
    s = _scored(planning, 0.8)  # exactly the true ratio
    assert s["verdict"] == "ready" and s["error_ratio"] == 0.0


def test_scorecard_newsvendor_harmful_when_worse(planning):
    assert _scored(planning, 1.6)["verdict"] == "harmful"


def test_scorecard_newsvendor_missing_fields_stay_not_yet(planning):
    recs = [{"type": "import_plan", "date": "2026-10-30", "raw_forecast_kwh": 10, "forecast_correction_factor": 1.0}]
    s = planning.shadow_scorecard(recs, "2026-11-01")["newsvendor_target"]
    assert s["verdict"] in ("no_records", "not_yet")
