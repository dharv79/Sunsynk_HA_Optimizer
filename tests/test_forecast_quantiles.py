"""Phase 70: self-learned P10/P50/P90 solar forecast (shadow)."""

from __future__ import annotations

from conftest import _planning as planning


def _day(i, ratio, band="shoulder", month=10, raw=10.0):
    return {"date": f"2026-{month:02d}-{i % 28 + 1:02d}", "raw_forecast_kwh": raw,
            "actual_solar_kwh": raw * ratio, "forecast_band": band}


def test_samples_skip_tiny_forecasts_and_cap():
    days = [_day(0, 1.2), _day(1, 1.0, raw=0.4), {"date": "2026-10-03", "raw_forecast_kwh": 5.0},
            _day(3, 20.0), {"date": "bad", "raw_forecast_kwh": 5.0, "actual_solar_kwh": 4.0}]
    samples = planning.forecast_ratio_samples(days)
    assert [s["ratio"] for s in samples] == [1.2, planning.FORECAST_QUANTILE_RATIO_CAP[1]]
    assert samples[0]["month"] == 10 and samples[0]["band"] == "shoulder"


def test_quantiles_from_the_narrowest_full_bin():
    days = [_day(i, 0.5 + i * 0.05) for i in range(21)]  # ratios 0.5 .. 1.5
    days += [_day(i, 3.0, band="sunny") for i in range(30)]
    samples = planning.forecast_ratio_samples(days)
    q = planning.forecast_quantiles(samples, 10, "shoulder", 8.0)
    assert q["bin"] == "band_season" and q["days"] == 21
    assert q["p50_ratio"] == 1.0 and q["p10_ratio"] == 0.6 and q["p90_ratio"] == 1.4
    assert q["p50_kwh"] == 8.0 and q["p10_kwh"] == 4.8


def test_falls_back_to_wider_bins_and_none_when_thin():
    samples = planning.forecast_ratio_samples([_day(i, 1.0, band="dull") for i in range(20)])
    assert planning.forecast_quantiles(samples, 10, "shoulder", 8.0)["bin"] == "season"
    assert planning.forecast_quantiles(samples, 4, "shoulder", 8.0)["bin"] == "all"
    assert planning.forecast_quantiles(samples[:5], 10, "dull", 8.0) is None
    assert planning.forecast_quantiles(samples, 10, None, None)["p50_kwh"] is None


def test_season_wraps_the_year():
    assert planning._season(1) == {12, 1, 2}
    assert planning._season(12) == {11, 12, 1}


def test_walk_forward_coverage():
    days = [_day(i, 1.0 + (i % 10) * 0.05) for i in range(40)]
    out = planning.forecast_quantile_coverage(planning.forecast_ratio_samples(days))
    assert out["days"] == 40 - planning.FORECAST_QUANTILE_MIN_DAYS
    assert round(out["below_p10"] + out["inside"] + out["above_p90"], 2) == 1.0
    assert planning.forecast_quantile_coverage([]) == {"days": 0, "below_p10": None, "inside": None, "above_p90": None}


def test_scale_hourly():
    assert planning.scale_hourly({12: 2.0}, 0.5) == {12: 1.0}
    assert planning.scale_hourly({12: 2.0}, None) == {12: 2.0}
    assert planning.scale_hourly(None, 0.5) is None


def test_plan_field_is_logged():
    from conftest import _data_logger

    assert "forecast_quantiles" in _data_logger._IMPORT_PLAN_FIELDS


def _cloudy(i, ratio, cloud, **kw):
    return {**_day(i, ratio, **kw), "cloud_cover_pct": cloud}


def test_cloud_bucket_edges_and_missing():
    b = planning.cloud_bucket
    assert (b(0), b(34.9), b(35), b(69.9), b(70), b(100)) == ("clear", "clear", "mixed", "mixed", "overcast", "overcast")
    assert b(None) is None and b("x") is None and b(True) is None


def test_cloud_bins_narrowest_first_and_sparse_fallback():
    days = [_cloudy(i, 0.4, 90) for i in range(6)] + [_cloudy(i, 1.2, 10) for i in range(15)]
    samples = planning.forecast_ratio_samples(days)
    q = planning.forecast_quantiles(samples, 10, "shoulder", 8.0, cloud="overcast")
    assert q["bin"] == "cloud_season" and q["days"] == 6 and q["p50_ratio"] == 0.4
    # Only 4 mixed days: below CLOUD_BIN_MIN_DAYS, so the band x season bin answers.
    thin = planning.forecast_ratio_samples([_cloudy(i, 1.0, 50) for i in range(4)] + days)
    assert planning.forecast_quantiles(thin, 10, "shoulder", 8.0, cloud="mixed")["bin"] == "band_season"
    # No cloud given: identical to phase 70 (cloud bins skipped).
    assert planning.forecast_quantiles(samples, 10, "shoulder", 8.0)["bin"] == "band_season"
    assert planning.forecast_quantiles(samples, 10, "shoulder", 8.0, cloud="clear")["days"] == 15


def test_samples_without_weather_have_no_cloud():
    s = planning.forecast_ratio_samples([_day(0, 1.0)])
    assert s[0]["cloud"] is None


def test_weather_fields_round_trip():
    from conftest import _data_logger

    for f in ("forecast_quantiles_cloud", "cloud_cover_pct", "outdoor_temp_c"):
        assert f in _data_logger._IMPORT_PLAN_FIELDS


def test_scorecard_weather_bins_entry():
    recs = []
    for i in range(8):
        d = f"2026-11-{i + 1:02d}"
        recs.append({"type": "import_plan", "date": d, "forecast_quantiles": {"p50_kwh": 8.0},
                     "forecast_quantiles_cloud": {"p50_kwh": 4.2}})
        recs.append({"type": "day_actuals", "date": d, "actual_solar_kwh": 4.0})
    card = planning.shadow_scorecard(recs, "2026-11-09")
    assert card["weather_bins"]["verdict"] == "ready" and card["weather_bins"]["error_ratio"] < 0.1
    assert planning.shadow_scorecard([], "2026-11-09")["weather_bins"]["days"] == 0
