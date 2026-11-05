"""Phase 77: flexibility-market export event offers (shadow)."""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import _planning as planning

NOW = datetime(2026, 7, 10, 17, 0, tzinfo=timezone.utc)


def _offer(**kw):
    args = dict(
        soc=95.0, reserve_soc=40, battery_capacity_kwh=10.0, hours=1.0, house_load_kw=1.0,
        price_pence=60.0, offpeak_pence=10.0, export_rate_kw=3.0,
    )
    args.update(kw)
    return planning.flex_event_offer(**args)


def test_offer_capped_by_export_rate_and_keeps_reserve():
    out = _offer()
    # Floor = reserve 40 + 10% house load over the hour + 5 margin = 55 → 4 kWh surplus, capped at 3 kWh by the rate.
    assert out["decision"] == "offer"
    assert out["floor_soc"] == 55
    assert out["offer_kwh"] == 3.0
    assert out["value_gbp"] == 1.8
    assert out["gain_gbp"] == round(1.8 - 3.0 * 10.0 / planning.ROUND_TRIP_EFFICIENCY / 100, 2)


def test_offer_limited_by_surplus_over_long_event():
    out = _offer(hours=2.0, soc=70.0)
    # Floor = 40 + 20 + 5 = 65 → 0.5 kWh surplus.
    assert out["decision"] == "offer" and out["offer_kwh"] == 0.5


def test_no_surplus_not_worth_no_price_no_soc_slots_held():
    assert _offer(soc=50.0)["decision"] == "no_surplus"
    assert _offer(soc=50.0)["value_gbp"] is None
    assert _offer(price_pence=11.0)["decision"] == "not_worth_it"
    assert _offer(price_pence=None)["decision"] == "no_price"
    assert _offer(soc=None)["decision"] == "no_soc"
    held = _offer(slots_held=True)
    assert held["decision"] == "slots_held" and held["offer_kwh"] == 0.0


def test_wear_can_make_it_not_worth_it():
    assert _offer(price_pence=14.0)["decision"] == "offer"
    assert _offer(price_pence=14.0, wear_pence=5.0)["decision"] == "not_worth_it"


def test_window_from_calendar_attributes():
    attrs = {"start_time": "2026-07-10 17:00:00", "end_time": "2026-07-10 18:30:00"}
    assert planning.flex_event_window(attrs, NOW, timezone.utc) == ("17:00", 1.5)


def test_window_on_off_entity_and_caps():
    assert planning.flex_event_window({}, NOW) == ("17:00", planning.FLEX_DEFAULT_HOURS)
    long = {"start_time": "2026-07-10T17:00:00+00:00", "end_time": "2026-07-11T17:00:00+00:00"}
    assert planning.flex_event_window(long, NOW)[1] == planning.FLEX_MAX_HOURS
    ending = {"start_time": "2026-07-10T16:00:00+00:00", "end_time": "2026-07-10T17:05:00+00:00"}
    assert planning.flex_event_window(ending, NOW) == ("16:00", 0.5)
    assert planning.flex_event_window(None, NOW)[0] == "17:00"
