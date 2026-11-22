"""Phase 105: weather-normalised load fit, daily comparison and monthly report (pure)."""
import random
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
    text = planning.load_report_text(rep)
    assert text.startswith("2026-10 load averaged")
    assert "kWh" in text and "kwh" not in text  # str.capitalize() used to lowercase the rest of the string


def _noisy_rows(n, base, slope, end, bump, bump_from, seed, noise_sd):
    rng = random.Random(seed)
    out = []
    for k in range(n):
        d = end - timedelta(days=k)
        temp = 3.0 + (k * 7) % 13
        load = base + slope * max(0.0, 15.5 - temp)
        if bump_from and d >= bump_from:
            load += bump
        load += rng.gauss(0, noise_sd)
        out.append({"date": d.isoformat(), "load_kwh": max(0.1, load), "temp_c": temp})
    return sorted(out, key=lambda r: r["date"])


def test_base_flag_needs_significance_not_just_size(planning):
    # A real, essentially noise-free base shift must flag: diff_se == 0 is the most significant
    # case, not the least, so LOAD_FLAG_SIGMA * 0 == 0 and any genuine difference still clears it.
    clean = _rows(120, bump=1.5, bump_from=date(2026, 10, 1))
    rep_clean = planning.monthly_load_report(clean, "2026-10")
    assert abs(rep_clean["base_shift_pct"]) >= planning.LOAD_BASE_SHIFT_PCT
    assert rep_clean["base_flag"] is True

    # The same size of shift, swamped by noise far bigger than the signal, must NOT flag: the raw
    # percent move alone clears LOAD_BASE_SHIFT_PCT, but it is not statistically significant.
    noisy = _noisy_rows(120, base=8.0, slope=0.8, end=date(2026, 11, 12), bump=1.5,
                         bump_from=date(2026, 10, 1), seed=7, noise_sd=6.0)
    rep_noisy = planning.monthly_load_report(noisy, "2026-10")
    assert rep_noisy["base_shift_pct"] is not None
    assert abs(rep_noisy["base_shift_pct"]) >= planning.LOAD_BASE_SHIFT_PCT
    assert rep_noisy["base_flag"] is False


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
