"""Phase 114: daytime hold floor on a native timer slot (pure logic)."""

SLOTS = [{"time": t} for t in ("02:00", "05:00", "16:00", "16:15", "16:20", "16:25")]


def test_slot_covering_now_to_16(planning):
    assert planning.daytime_hold_slot(SLOTS, 9 * 60) == 2
    assert planning.daytime_hold_slot(SLOTS, 15 * 60 + 59) == 2
    split = [{"time": t} for t in ("02:00", "05:00", "13:00", "16:00", "16:15", "16:20")]
    assert planning.daytime_hold_slot(split, 9 * 60) is None  # slot 2 ends 13:00, before 16:00
    assert planning.daytime_hold_slot(split, 14 * 60) == 3
    assert planning.daytime_hold_slot(SLOTS, 5 * 60) == 2
    assert planning.daytime_hold_slot(SLOTS, 3 * 60) is None  # slot 1 ends 05:00, before 16:00


def test_slot_unreadable_is_none(planning):
    assert planning.daytime_hold_slot(None, 600) is None
    assert planning.daytime_hold_slot([{"time": "x"}, {"time": "05:00"}], 600) is None
    assert planning.daytime_hold_slot([{"time": "05:00"}], 600) is None


def test_hold_floor_only_for_hold_and_only_up(planning):
    assert planning.daytime_hold_floor("hold", 66, 20, 69) == 66
    assert planning.daytime_hold_floor("top_up", 66, 20, 69) is None
    assert planning.daytime_hold_floor("hold", 66, 70, 80) is None  # floor already higher
    assert planning.daytime_hold_floor("hold", 66, 20, 60) is None  # would force a grid charge
    assert planning.daytime_hold_floor("hold", 130, 20, 100) == 100
    assert planning.daytime_hold_floor("hold", 66, None, 69) is None
    assert planning.daytime_hold_floor("hold", None, 20, 69) is None


def test_readback(planning):
    assert planning.hold_readback_ok({"cap2": "66"}, 2, 66) is True
    assert planning.hold_readback_ok({"cap2": "20"}, 2, 66) is False
    assert planning.hold_readback_ok({}, 2, 66) is None
    assert planning.hold_readback_ok(None, 2, 66) is None


def test_reassert_only_when_below_hold_and_keep_user_raise(planning):
    # Phase 122: the user raised cap3 to 65 over a 46% hold; neither the 30-min check nor 16:00 touches it.
    assert not planning.hold_floor_needs_reassert({"cap3": "65"}, 3, 46)
    assert not planning.hold_floor_needs_reassert({"cap3": "46"}, 3, 46)
    assert planning.hold_floor_needs_reassert({"cap3": "20"}, 3, 46)
    assert not planning.hold_floor_needs_reassert(None, 3, 46)
    assert not planning.hold_floor_restore_due({"cap3": "65"}, 3, 46)
    assert planning.hold_floor_restore_due({"cap3": "46"}, 3, 46)
    assert planning.hold_floor_restore_due(None, 3, 46)
