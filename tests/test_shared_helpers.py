"""Phase 97: characterisation of the shared maths helpers and tests for formerly untested functions."""
import pytest


@pytest.mark.parametrize("values,pct,expected", [
    ([5.0], 50, 5.0),
    ([1.0, 2.0, 3.0, 4.0], 50, 2.5),
    ([4.0, 1.0, 3.0, 2.0], 75, 3.25),
    ([1.0, 2.0, 3.0], 0, 1.0),
    ([1.0, 2.0, 3.0], 100, 3.0),
])
def test_percentile_linear_interpolation(planning, values, pct, expected):
    assert planning.percentile(values, pct) == pytest.approx(expected)
    assert planning._percentile(values, pct) == pytest.approx(expected)
    assert planning._quantile(sorted(values), pct / 100.0) == pytest.approx(expected)


def test_percentile_empty_uses_default(planning):
    assert planning.percentile([], 50, default=0.0) == 0.0
    with pytest.raises(ValueError):
        planning.percentile([], 50)


def test_median(planning):
    assert planning.median([3.0, 1.0, 2.0]) == 2.0
    assert planning.median([4.0, 1.0, 2.0, 3.0]) == 2.5
    assert planning._median([7.0]) == 7.0


def test_peak_load_estimate_pinned(planning):
    vals = [2.0, 3.0, 4.0, 5.0, 9.0, 1.0, 6.0]
    assert planning.peak_load_estimate_kwh(vals, 4.0) == (5.5, "history")
    assert planning.peak_load_estimate_kwh([1.0], 4.0) == (4.0, "default")


def test_hhmm_to_minutes_shared(planning):
    assert planning.hhmm_to_minutes("05:30") == 330
    assert planning.hhmm_to_minutes("05:30:10") == 330
    assert planning.hhmm_to_minutes("bad") is None


def test_round_or_none(planning):
    assert planning.round_or_none(None) is None
    assert planning.round_or_none(1.2345) == 1.23
    assert planning.round_or_none(1.2355, 3) == 1.236 or planning.round_or_none(1.2355, 3) == 1.235


def test_band_key_and_price_at(planning):
    assert planning.band_key_at(3 * 60) == "offpeak"
    assert planning.band_key_at(17 * 60) == "peak"
    assert planning.band_key_at(12 * 60) == "day"
    bands = [(120, 300, 7.0), (1380, 60, 9.0)]  # second band wraps midnight
    assert planning.price_at(bands, 200) == 7.0
    assert planning.price_at(bands, 1400) == 9.0
    assert planning.price_at(bands, 30) == 9.0
    assert planning.price_at(bands, 600) is None


def test_free_event_sold(planning):
    assert planning.free_event_sold({"soc_at_sell_start": 80}) is True
    assert planning.free_event_sold({"phase": "scheduled"}) is False


def test_mpc_load_kw(planning):
    loads = {"overnight": 0.5, "day": 1.0, "evening": 2.0}
    assert planning.mpc_load_kw(23 * 60, loads) == 0.5
    assert planning.mpc_load_kw(3 * 60, loads) == 0.5
    assert planning.mpc_load_kw(12 * 60, loads) == 1.0
    assert planning.mpc_load_kw(18 * 60, loads) == 2.0
    assert planning.mpc_load_kw(24 * 60 + 12 * 60, loads) == 1.0


def test_upgrade_scenarios(planning):
    no_pv = planning.upgrade_scenarios(10.0, None)
    assert no_pv[0] == {"name": "current", "capacity_kwh": 10.0, "pv_scale": 1.0}
    assert all("added_kwp" not in s for s in no_pv)
    with_pv = planning.upgrade_scenarios(10.0, 4.0)
    assert len(with_pv) == len(no_pv) + 1
    pv = with_pv[-1]
    assert pv["capacity_kwh"] == 10.0 and pv["pv_scale"] == pytest.approx((4.0 + pv["added_kwp"]) / 4.0)
