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


# --- Phase 35: standing charges, VAT, Intelligent, Agile ---------------------

FLAT_BANDS = [(0, 1440, 10.0)]
NO_EXPORT = [(0, 1440, 0.0)]


def _month(days=2):
    return {f"2026-09-0{i + 1}": list(FLAT) for i in range(days)}


def test_standing_charge_times_days(planning):
    out = planning.compare_tariffs(_month(2), _month(2), {"a": (FLAT_BANDS, NO_EXPORT)}, "2026-09", standing={"a": 50.0})
    # 48 kWh × 10p = £4.80/day; + 50p/day standing.
    assert out["net_gbp"]["a"] == 9.6
    assert out["total_gbp"]["a"] == 10.6


def test_vat_applies_to_import_and_standing_not_export(planning):
    export = [(0, 1440, 1.0)]  # 48p/day income
    out = planning.compare_tariffs(
        _month(1), _month(1), {"a": (FLAT_BANDS, export)}, "2026-09", standing={"a": 52.0}, vat_percent=5
    )
    assert out["total_gbp"]["a"] == round(((480 + 52) * 1.05 - 48) / 100, 2)
    off = planning.compare_tariffs(_month(1), _month(1), {"a": (FLAT_BANDS, export)}, "2026-09", standing={"a": 52.0})
    assert off["total_gbp"]["a"] == round((480 + 52 - 48) / 100, 2)


def test_missing_standing_gives_none_total_but_keeps_net(planning):
    out = planning.compare_tariffs(_month(1), _month(1), {"a": (FLAT_BANDS, NO_EXPORT)}, "2026-09")
    assert out["net_gbp"]["a"] == 4.8 and out["total_gbp"]["a"] is None


def test_agile_slot_pricing(planning):
    prices = [float(i) for i in range(48)]  # 0p..47p
    agile = {"2026-09-01": prices}
    out = planning.compare_tariffs(_month(1), _month(1), {"agile": (agile, NO_EXPORT)}, "2026-09", standing={"agile": 0})
    assert out["total_gbp"]["agile"] == round(sum(prices) / 100, 2)


def test_agile_unavailable_is_none_others_still_reported(planning):
    tariffs = {"agile": ({}, NO_EXPORT), "a": (FLAT_BANDS, NO_EXPORT)}
    out = planning.compare_tariffs(_month(1), _month(1), tariffs, "2026-09", standing={"agile": 50, "a": 50})
    assert out["total_gbp"]["agile"] is None and out["net_gbp"]["agile"] is None
    assert out["total_gbp"]["a"] == 5.3 and out["cheapest"] == "a"


def test_intelligent_cheap_window(planning):
    rates = planning.merge_tariff_rates(None)["intelligent"]
    bands = planning.bands_from_rates(planning.TARIFF_IMPORT_BANDS["intelligent"], rates)
    assert planning.band_price(bands, 23 * 60 + 30) == rates["cheap"]
    assert planning.band_price(bands, 5 * 60) == rates["cheap"]
    assert planning.band_price(bands, 5 * 60 + 30) == rates["day"]


def test_agile_prices_by_date_complete_days_only(planning):
    from datetime import timezone

    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        {"valid_from": (base + timedelta(minutes=30 * i)).isoformat().replace("+00:00", "Z"),
         "value_exc_vat": float(i % 48), "value_inc_vat": 99.0}
        for i in range(48 + 10)  # one full day + 10 slots of the next
    ]
    out = planning.agile_prices_by_date(rows, timezone.utc)
    assert list(out) == ["2026-09-01"] and out["2026-09-01"][47] == 47.0  # ex-VAT used
    assert planning.agile_prices_by_date(None) == {}


def test_overrides_vat_region_and_standing(planning):
    assert planning.tariff_vat_percent(None) == planning.TARIFF_VAT_PERCENT
    assert planning.tariff_vat_percent('{"vat_percent": 5}') == 5.0
    assert planning.tariff_vat_percent('{"vat_percent": "5"}') == planning.TARIFF_VAT_PERCENT
    assert planning.agile_region_override('{"agile_region": "c"}') == "C"
    assert planning.agile_region_override('{"agile_region": "Z"}') is None
    assert planning.merge_tariff_rates('{"flux": {"standing": 60}}')["flux"]["standing"] == 60.0
    assert planning.octopus_region("E-1R-FLUX-IMPORT-23-02-14-C") == "C"
    assert planning.octopus_region("bogus") is None


def test_newest_agile_product(planning):
    products = [
        {"code": "AGILE-22-08-31", "direction": "IMPORT", "available_from": "2022-08-31T00:00:00Z"},
        {"code": "AGILE-24-10-01", "direction": "IMPORT", "available_from": "2024-10-01T00:00:00Z"},
        {"code": "AGILE-OUTGOING-19-05-13", "direction": "EXPORT", "available_from": "2019-05-13T00:00:00Z"},
        {"code": "AGILE-27-01-01", "direction": "IMPORT", "available_from": "2027-01-01T00:00:00Z"},
        {"code": "GO-VAR-22-10-14", "direction": "IMPORT", "available_from": "2022-10-14T00:00:00Z"},
    ]
    assert planning.newest_agile_product(products, datetime(2026, 9, 1)) == "AGILE-24-10-01"
    assert planning.newest_agile_product([], datetime(2026, 9, 1)) is None
