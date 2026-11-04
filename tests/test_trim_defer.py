"""Phase 65: trim only the overflow, defer the rest to the peak."""


def test_no_forecast_trims_in_full(planning):
    r = planning.trim_defer_kwh(95, 82, 10.0, None, 0.5, 5.0)
    assert r["defer_kwh"] == 0.0 and r["needed_soc"] == 82 and r["full_trim_kwh"] == 1.3


def test_clipped_today_trims_in_full(planning):
    r = planning.trim_defer_kwh(95, 82, 10.0, 0.0, 0.5, 5.0, clipped_today=True)
    assert r["defer_kwh"] == 0.0 and r["reason"] == "clipped_today"


def test_dull_day_defers_all(planning):
    # remaining PV 1 kWh, load 2.5 kWh -> no surplus
    r = planning.trim_defer_kwh(95, 82, 10.0, 1.0, 0.5, 5.0)
    assert r["needed_kwh"] == 0.0 and r["defer_kwh"] == 1.3 and r["needed_soc"] == 95 - 0


def test_partial_overflow_trims_only_overflow(planning):
    # surplus 4 - 2.5 = 1.5... use PV 5, load 2.5 -> 2.5; headroom 0.5 -> overflow 2.0 > full 1.3 -> trim in full
    assert planning.trim_defer_kwh(95, 82, 10.0, 5.0, 0.5, 5.0)["defer_kwh"] == 0.0
    # PV 3.5 -> surplus 1.0, headroom 0.5 -> overflow 0.5; defer 0.8
    r = planning.trim_defer_kwh(95, 82, 10.0, 3.5, 0.5, 5.0)
    assert r["needed_kwh"] == 0.5 and r["defer_kwh"] == 0.8 and r["needed_soc"] == 90


def test_small_deferral_ignored(planning):
    # overflow 1.1 of full 1.3 -> defer 0.2 < 0.3 -> full trim
    r = planning.trim_defer_kwh(95, 82, 10.0, 4.1, 0.5, 5.0)
    assert r["defer_kwh"] == 0.0 and r["needed_soc"] == 82
