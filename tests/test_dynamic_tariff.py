"""Phase 76: dynamic-tariff native mode (Agile half-hourly prices in the MPC, shadow)."""

from __future__ import annotations

from conftest import _planning as planning

PRICES = {"offpeak": 10.0, "day": 30.0, "peak": 40.0}
LOADS = {"overnight": 0.5, "day": 0.6, "evening": 1.2}


def _agile(cheap_from=4, cheap_to=10, cheap=5.0, dear=25.0, spike=None):
    day = [dear] * 48
    for i in range(cheap_from, cheap_to):
        day[i] = cheap
    if spike is not None:
        for i in range(33, 38):
            day[i] = spike
    return day


def test_cheapest_window_fills_cheapest_slots_first():
    prices = [20.0] * 48
    prices[6], prices[7], prices[8] = 4.0, 3.0, 5.0
    plan = planning.cheapest_window_plan(prices, 3.0, 3.0)  # 1.5 kWh per half hour → 2 slots
    assert plan["slots"] == [6, 7]
    assert plan["windows"] == [["03:00", "04:00"]]
    assert plan["kwh"] == 3.0 and plan["short_kwh"] == 0.0
    assert plan["avg_p"] == 3.5


def test_cheapest_window_last_slot_partial_and_split_windows():
    prices = [20.0] * 48
    prices[2], prices[30] = 1.0, 2.0
    plan = planning.cheapest_window_plan(prices, 2.0, 3.0)
    assert plan["slots"] == [2, 30]
    assert plan["windows"] == [["01:00", "01:30"], ["15:00", "15:30"]]
    assert plan["kwh"] == 2.0


def test_cheapest_window_spike_guard_and_shortfall():
    prices = [None] * 46 + [50.0, 60.0]
    plan = planning.cheapest_window_plan(prices, 3.0, 3.0, max_price_p=40.0)
    assert plan["slots"] == [] and plan["short_kwh"] == 3.0 and plan["avg_p"] is None


def test_cheapest_window_always_takes_negative_slots():
    prices = [10.0] * 48
    prices[26], prices[27], prices[28] = -2.0, -1.0, -0.5
    plan = planning.cheapest_window_plan(prices, 0.5, 3.0)
    assert plan["slots"] == [26, 27, 28]
    assert plan["plunge_slots"] == 3


def test_cheapest_window_from_slot_and_two_days():
    prices = _agile() + _agile(cheap=2.0)
    plan = planning.cheapest_window_plan(prices, 3.0, 3.0, from_slot=44)
    assert all(i >= 48 for i in plan["slots"])  # tomorrow's cheaper night, not today's past one
    assert plan["windows"][0][0] == "02:00"


def test_cheapest_window_without_prices_or_rate():
    assert planning.cheapest_window_plan(None, 3.0, 3.0) is None
    assert planning.cheapest_window_plan([1.0], 3.0, 0) is None


def test_mpc_slots_price_from_vectors_and_stop_where_they_end():
    slots = planning.mpc_slots(22 * 60, PRICES, [], {}, {}, LOADS, import_vectors={0: _agile()}, export_flat_p=12.0)
    assert len(slots) == 4  # 22:00-24:00 only: no tomorrow vector
    assert {s["import_p"] for s in slots} == {25.0}
    assert {s["export_p"] for s in slots} == {12.0}
    both = planning.mpc_slots(22 * 60, PRICES, [], {}, {}, LOADS, import_vectors={0: _agile(), 1: _agile()})
    assert len(both) == 48
    assert both[10]["import_p"] == 5.0  # 03:00 tomorrow is in the cheap run


def test_mpc_slots_without_vectors_unchanged():
    a = planning.mpc_slots(600, PRICES, [], {}, {}, LOADS)
    b = planning.mpc_slots(600, PRICES, [], {}, {}, LOADS, import_vectors=None)
    assert a == b


def test_agile_mpc_charges_in_cheap_slots_and_summary():
    vectors = {0: _agile(), 1: _agile(spike=60.0)}
    agile_slots = planning.mpc_slots(22 * 60, PRICES, [], {}, {}, LOADS, import_vectors=vectors, export_flat_p=12.0)
    flux_slots = planning.mpc_slots(22 * 60, PRICES, [], {}, {}, LOADS)
    agile = planning.mpc_solve(agile_slots, 30, 10.0, 3.0, terminal_pence=10.0)
    flux = planning.mpc_solve(flux_slots, 30, 10.0, 3.0, terminal_pence=10.0)
    charged = [s["time"] for s in agile["slots"] if s["action"] == "grid_charge"]
    assert charged and all("02:00" <= t < "05:00" for t in charged)
    prices = vectors[0][44:] + vectors[1][:44]
    windows = planning.cheapest_window_plan(vectors[0] + vectors[1], 3.0, 3.0, from_slot=44)
    summary = planning.dynamic_tariff_summary(flux, agile, prices, windows)
    assert summary["saving_gbp"] == round(flux["cost_gbp"] - agile["cost_gbp"], 2)
    assert summary["spike_slots"] == 5 and summary["max_p"] == 60.0 and summary["min_p"] == 5.0
    assert summary["cheapest_windows"] == [["02:00", "03:00"]]


def test_summary_missing_plan_is_none_not_zero():
    summary = planning.dynamic_tariff_summary(None, None, [], None)
    assert summary["saving_gbp"] is None and summary["flux_cost_gbp"] is None
    assert summary["min_p"] is None and summary["spike_slots"] == 0
