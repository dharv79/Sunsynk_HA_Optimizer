"""Tests for phase 28: half-hourly tariff costing and comparison."""

from __future__ import annotations

from datetime import datetime, timedelta

FLAT = [1.0] * 48


def _charges(day="2026-10-11", offset="+01:00", kwh=0.5):
    base = datetime.fromisoformat(f"{day}T00:00:00{offset}")
    return {"charges": [
        {"start": (base + timedelta(minutes=30 * i)).isoformat(),
         "end": (base + timedelta(minutes=30 * i + 30)).isoformat(), "consumption": kwh}
        for i in range(48)
    ]}


def _go(planning):
    rates = planning.merge_tariff_rates(None)["go"]
    return planning.bands_from_rates(planning.TARIFF_IMPORT_BANDS["go"], rates)


def test_band_boundaries_and_wrap(planning):
    bands = _go(planning)
    assert planning.band_price(bands, 29) == 28.0       # 00:29 still day rate (wraps midnight)
    assert planning.band_price(bands, 30) == 8.5        # 00:30 starts cheap
    assert planning.band_price(bands, 5 * 60 + 29) == 8.5
    assert planning.band_price(bands, 5 * 60 + 30) == 28.0


def test_cost_under_tariff_sums_slots(planning):
    # Go: 10 cheap slots (00:30-05:30) + 38 day slots, 1 kWh each.
    assert planning.cost_under_tariff(FLAT, _go(planning)) == round(10 * 8.5 + 38 * 28.0, 2)


def test_missing_slot_gives_none_never_partial(planning):
    kwh = list(FLAT)
    kwh[7] = None
    assert planning.cost_under_tariff(kwh, _go(planning)) is None
    assert planning.cost_under_tariff(FLAT[:47], _go(planning)) is None
    assert planning.cost_under_tariff(None, _go(planning)) is None
    # A gap in the bands also gives None.
    assert planning.cost_under_tariff(FLAT, [(0, 60, 10.0)]) is None


def test_parse_charges_complete_day(planning):
    day, kwh = planning.parse_half_hourly_charges(_charges())
    assert day == "2026-10-11" and len(kwh) == 48 and kwh[0] == 0.5


def test_parse_charges_rejects_incomplete(planning):
    short = _charges()
    short["charges"].pop()
    assert planning.parse_half_hourly_charges(short) is None
    assert planning.parse_half_hourly_charges({}) is None
    assert planning.parse_half_hourly_charges(None) is None
    bad = _charges()
    bad["charges"][3]["consumption"] = None
    assert planning.parse_half_hourly_charges(bad) is None
    dup = _charges()
    dup["charges"][4] = dict(dup["charges"][3])
    assert planning.parse_half_hourly_charges(dup) is None


def test_compare_pairs_days_and_nets_export(planning):
    imp = {"2026-10-11": FLAT, "2026-10-12": FLAT}
    exp = {"2026-10-11": [0.1] * 48}  # 12th export not settled yet
    go = _go(planning)
    flat_export = [(0, 1440, 12.0)]
    out = planning.compare_tariffs(imp, exp, {"go": (go, flat_export)}, "2026-10")
    assert out["days_compared"] == 1 and out["days_missing"] == 1
    expected = round((10 * 8.5 + 38 * 28.0 - 48 * 0.1 * 12.0) / 100, 2)
    assert out["net_gbp"]["go"] == expected and out["cheapest"] == "go"


def test_compare_tariff_that_cannot_price_is_none(planning):
    out = planning.compare_tariffs(
        {"2026-10-11": FLAT}, {"2026-10-11": FLAT},
        {"ok": ([(0, 1440, 10.0)], [(0, 1440, 5.0)]), "gap": ([(0, 60, 10.0)], [(0, 1440, 5.0)])},
        "2026-10",
    )
    assert out["net_gbp"]["gap"] is None and out["net_gbp"]["ok"] == 2.4
    assert out["cheapest"] == "ok"
    empty = planning.compare_tariffs({}, {}, {"ok": ([(0, 1440, 1.0)], [(0, 1440, 1.0)])}, "2026-10")
    assert empty["net_gbp"]["ok"] is None and empty["cheapest"] is None


def test_other_months_ignored(planning):
    out = planning.compare_tariffs(
        {"2026-09-30": FLAT}, {"2026-09-30": FLAT}, {"a": ([(0, 1440, 1.0)], [(0, 1440, 1.0)])}, "2026-10"
    )
    assert out["days_compared"] == 0


def test_flux_bands_from_user_charges(planning):
    charges = [
        {"price": 16.66, "startRange": "02:00", "endRange": "05:00", "status": "import"},
        {"price": 27.77, "startRange": "05:00", "endRange": "02:00", "status": "import"},
        {"price": 4.39, "startRange": "02:00", "endRange": "05:00", "status": "export"},
    ]
    imp = planning.flux_bands_from_charges(charges, "import")
    assert planning.band_price(imp, 3 * 60) == 16.66 and planning.band_price(imp, 23 * 60) == 27.77
    assert planning.flux_bands_from_charges("x", "import") == []


def test_overrides_merge_and_ignore_junk(planning):
    r = planning.merge_tariff_rates('{"go": {"cheap": 7.5, "bogus": 1, "day": "x"}, "nope": {"a": 1}}')
    assert r["go"]["cheap"] == 7.5 and r["go"]["day"] == 28.0 and "bogus" not in r["go"]
    assert planning.merge_tariff_rates("not json")["go"]["cheap"] == 8.5
    assert planning.merge_tariff_rates(None)["cosy"]["peak"] == 40.5
