"""Phase 105: weather-normalised load fit, daily comparison and monthly report (pure)."""
from datetime import date, timedelta


def _rows(n, base=8.0, slope=0.8, end=date(2026, 11, 12), bump=0.0, bump_from=None):
    out = []
    for k in range(n):
        d = end - timedelta(days=k)
        temp = 3.0 + (k * 7) % 13  # 3..15 °C, no trend with date
        load = base + slope * max(0.0, 15.5 - temp)
        if bump_from and d >= bump_from:
            load += bump
        out.append({"date": d.isoformat(), "load_kwh": load, "temp_c": temp})
    return sorted(out, key=lambda r: r["date"])


def test_fit_recovers_base_and_slope(planning):
    fit = planning.fit_load_temp(_rows(60))
    assert abs(fit["base_kwh"] - 8.0) < 0.05 and abs(fit["slope_kwh_per_c"] - 0.8) < 0.01


def test_too_few_days_none(planning):
    assert planning.fit_load_temp(_rows(20)) is None
    assert planning.load_vs_expected(_rows(20), "2026-11-12", 20, 5) is None


def test_negative_slope_is_flat(planning):
    rows = _rows(40, slope=0.0)
    for r in rows:
        r["load_kwh"] = 20 - 0.3 * max(0.0, 15.5 - r["temp_c"])
    fit = planning.fit_load_temp(rows)
    assert fit["slope_kwh_per_c"] == 0.0


def test_load_vs_expected_excludes_own_day(planning):
    rows = _rows(60)
    res = planning.load_vs_expected(rows, "2026-11-12", 20.0, 5.0)
    expected = 8.0 + 0.8 * 10.5
    assert abs(res["expected_kwh"] - expected) < 0.1 and res["diff_kwh"] > 3
    assert res["pct"] > 15


def test_month_flagged_when_higher_at_same_temperature(planning):
    rows = _rows(120, bump=3.0, bump_from=date(2026, 10, 1))  # Oct and Nov up 3 kWh
    rep = planning.monthly_load_report(rows, "2026-10")
    assert rep["this"]["flag"] == "higher" and rep["this"]["pct"] > 8
    assert planning.load_report_text(rep).startswith("2026-10 load averaged")


def test_quiet_month_says_nothing(planning):
    rep = planning.monthly_load_report(_rows(120), "2026-10")
    assert rep["this"]["flag"] is None and planning.load_report_text(rep) is None
    assert rep["last_year"] is None


def test_prior_month_wraps_year(planning):
    assert planning.prior_month("2026-01") == "2025-12"
    assert planning.prior_month("2026-03", 12) == "2025-03"


def test_rows_skip_away_and_missing(planning):
    recs = [
        {"type": "day_actuals", "date": "2026-11-01", "day_load_kwh": 10.0},
        {"type": "day_kpis", "date": "2026-11-01", "outside_temp_c": 5.0},
        {"type": "day_actuals", "date": "2026-11-02", "day_load_kwh": 3.0},
        {"type": "day_kpis", "date": "2026-11-02", "outside_temp_c": 5.0},
        {"type": "import_plan", "date": "2026-11-02", "away": True},
        {"type": "day_actuals", "date": "2026-11-03", "day_load_kwh": 9.0},
    ]
    assert planning.weather_load_rows(recs) == [{"date": "2026-11-01", "load_kwh": 10.0, "temp_c": 5.0}]
