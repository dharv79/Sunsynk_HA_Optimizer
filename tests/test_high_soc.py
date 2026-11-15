"""Phase 116: time at high state of charge (pure logic)."""


def _s(*pairs):
    return [{"minute": m, "soc": soc} for m, soc in pairs]


def test_hours_credit_the_earlier_sample(planning):
    out = planning.high_soc_hours(_s((0, 90), (30, 96), (60, 100), (90, 100), (120, 80)))
    assert out == {"high_soc_hours": 1.5, "full_soc_hours": 1.0, "high_soc_sample_hours": 2.0}


def test_too_few_or_unusable_samples_is_none(planning):
    assert planning.high_soc_hours(None) is None
    assert planning.high_soc_hours(_s((0, 99))) is None
    assert planning.high_soc_hours(_s((0, None), (30, None))) is None


def test_gap_is_not_credited_and_partial_day_reports_coverage(planning):
    out = planning.high_soc_hours(_s((0, 99), (30, 99), (600, 99), (630, 50)))
    assert out["high_soc_hours"] == 1.0 and out["high_soc_sample_hours"] == 1.0  # the 570-minute gap is skipped


def test_threshold_is_inclusive(planning):
    assert planning.high_soc_hours(_s((0, 95), (60, 95)))["high_soc_hours"] == 1.0
    assert planning.high_soc_hours(_s((0, 94), (60, 94)))["high_soc_hours"] == 0.0


def test_totals_count_only_days_with_the_field(planning):
    recs = [
        {"type": "day_kpis", "date": "2026-11-10", "high_soc_hours": 2.0},
        {"type": "day_kpis", "date": "2026-11-11"},  # logged before phase 116
        {"type": "day_kpis", "date": "2026-09-01", "high_soc_hours": 4.0},
        {"type": "day_kpis", "date": "2026-01-02", "high_soc_hours": 1.0},
        {"type": "hourly_profile", "date": "2026-11-12", "high_soc_hours": 9},
    ]
    out = planning.high_soc_totals(recs, "2026-11-15")
    assert out == {"high_soc_30d_hours": 2.0, "high_soc_30d_days": 1, "high_soc_ytd_hours": 7.0, "high_soc_ytd_days": 3}
    empty = planning.high_soc_totals([], "2026-11-15")
    assert empty["high_soc_30d_hours"] is None and empty["high_soc_ytd_days"] == 0
    assert planning.high_soc_totals(recs, "bad")["high_soc_30d_hours"] is None
