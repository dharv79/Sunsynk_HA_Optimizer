"""Phase 73: native inverter time-of-use slots (shadow)."""

from __future__ import annotations

from conftest import _planning as planning


def _times(slots):
    return [s["time"] for s in slots]


def test_plan_maps_to_six_slots():
    slots = planning.tou_slots_from_plan("02:00", "04:30", 85, 25, 40)
    assert len(slots) == 6
    assert _times(slots) == sorted(_times(slots))
    by_time = {s["time"]: s for s in slots}
    # Midnight runs the house on the battery to the charge; only 16:00-19:00 holds the evening reserve.
    assert by_time["00:00"] == {"time": "00:00", "soc": 25, "grid_charge": False}
    assert by_time["02:00"] == {"time": "02:00", "soc": 85, "grid_charge": True}
    assert by_time["04:30"] == {"time": "04:30", "soc": 25, "grid_charge": False}
    assert by_time["16:00"] == {"time": "16:00", "soc": 40, "grid_charge": False}
    assert by_time["19:00"] == {"time": "19:00", "soc": 25, "grid_charge": False}
    # Only the charge window charges from the grid.
    assert [s["time"] for s in slots if s["grid_charge"]] == ["02:00"]


def test_padding_splits_on_the_half_hour_and_keeps_settings():
    slots = planning.tou_slots_from_plan("02:00", "05:00", 90, 25, 40)
    for s in slots:
        assert int(s["time"][3:]) in (0, 30)
    # Every padded slot repeats its neighbour's settings, so behaviour is unchanged.
    expanded = {}
    for s in slots:
        expanded[s["time"]] = (s["soc"], s["grid_charge"])
    assert expanded["02:00"] == (90, True)
    after = [v for t, v in expanded.items() if "05:00" <= t < "16:00"]
    assert after and set(after) == {(25, False)}


def test_peak_hold_slot_and_merge():
    slots = planning.tou_slots_from_plan("02:00", "05:00", 90, 25, 40, peak_hold=("13:45", 70))
    by_time = {s["time"]: s for s in slots}
    assert by_time["13:45"]["soc"] == 70 and not by_time["13:45"]["grid_charge"]
    # Reserve equal to the floor: 16:00 and 19:00 merge into the floor run, then padding refills six slots.
    flat = planning.tou_slots_from_plan("02:00", "05:00", 90, 25, 25)
    assert len(flat) == 6
    assert sum(1 for s in flat if s["grid_charge"]) == 1


def test_plan_missing_inputs():
    assert planning.tou_slots_from_plan(None, "05:00", 90, 25, 40) is None
    assert planning.tou_slots_from_plan("02:00", "05:00", None, 25, 40) is None
    assert planning.tou_slots_from_plan("05:00", "02:00", 90, 25, 40) is None
    # No reserve → the floor.
    assert planning.tou_slots_from_plan("02:00", "05:00", 90, 25, None)[0]["soc"] == 25


def _settings(slots):
    return planning.tou_settings_payload(slots)


def test_settings_round_trip_and_diff():
    plan = planning.tou_slots_from_plan("02:00", "04:30", 85, 25, 40)
    payload = _settings(plan)
    assert payload["sellTime1"] == "00:00" and payload["time2on"] in ("true", "false")
    assert planning.tou_slots_from_settings(payload) == plan
    assert planning.tou_diff(plan, plan) == []
    other = [dict(s) for s in plan]
    other[1]["soc"] = 100
    diffs = planning.tou_diff(other, plan)
    assert diffs == [{"slot": 2, "field": "soc", "inverter": 100, "plan": 85}]


def test_settings_parse_variants_and_unreadable():
    payload = _settings(planning.tou_slots_from_plan("02:00", "04:30", 85, 25, 40))
    payload["time2on"] = True
    payload["cap3"] = 25.0
    payload["sellTime4"] = "08:00:00"
    parsed = planning.tou_slots_from_settings(payload)
    assert parsed[1]["grid_charge"] is True and parsed[2]["soc"] == 25 and parsed[3]["time"] == "08:00"
    missing = dict(payload)
    del missing["cap6"]
    assert planning.tou_slots_from_settings(missing) is None
    assert planning.tou_slots_from_settings({**payload, "time1on": "maybe"}) is None
    assert planning.tou_slots_from_settings(None) is None
    assert planning.tou_diff(None, [{}]) is None


def test_payload_needs_six_slots():
    assert planning.tou_settings_payload(None) is None
    assert planning.tou_settings_payload([{"time": "00:00", "soc": 25, "grid_charge": False}]) is None
