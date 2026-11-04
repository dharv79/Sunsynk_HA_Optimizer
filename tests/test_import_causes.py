"""Phase 42: import cause ledger."""


def _s(minute, soc, imp, **kw):
    return {"minute": minute, "soc": soc, "grid_import_kwh": imp, **kw}


def test_no_prev_or_unknown_values_not_attributed(planning):
    assert planning.import_cause_step(None, _s(600, 50, 1.0)) == (None, 0.0)
    assert planning.import_cause_step(_s(600, None, 1.0), _s(610, 50, 2.0)) == (None, 0.0)


def test_gap_reset_and_zero_import_not_attributed(planning):
    assert planning.import_cause_step(_s(600, 50, 1.0), _s(700, 50, 2.0)) == (None, 0.0)
    assert planning.import_cause_step(_s(600, 50, 5.0), _s(610, 50, 1.0)) == (None, 0.0)
    assert planning.import_cause_step(_s(600, 50, 1.0), _s(610, 50, 1.0)) == (None, 0.0)


def test_causes_in_order(planning):
    f = planning.import_cause_step
    assert f(_s(600, 50, 1.0), _s(610, 50, 1.5, slots_held=True)) == ("free_event", 0.5)
    assert f(_s(600, 50, 1.0), _s(610, 52, 1.5)) == ("grid_charge", 0.5)
    assert f(_s(600, 50, 1.0), _s(610, 48, 1.5)) == ("load_over_limit", 0.5)
    assert f(_s(600, 12, 1.0), _s(610, 11, 1.5)) == ("battery_floor", 0.5)
    assert f(_s(600, 50, 1.0), _s(610, 50, 1.5, slot_hold=True)) == ("slot_hold", 0.5)
    assert f(_s(130, 50, 1.0), _s(140, 50, 1.5)) == ("slot_hold", 0.5)  # 02:00-05:00 charge window
    assert f(_s(600, 50, 1.0), _s(610, 50, 1.5)) == ("unknown", 0.5)
