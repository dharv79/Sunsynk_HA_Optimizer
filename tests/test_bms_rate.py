"""Phase 113: BMS charge-limit rate input (pure logic)."""


def test_bms_limited_rate(planning):
    assert planning.bms_limited_rate_kw(50, 51.2) == 2.56
    assert planning.bms_limited_rate_kw(128, 51.2) == 4.61      # capped at the 90 A inverter limit
    assert planning.bms_limited_rate_kw(50, None) == 2.56        # nominal voltage
    assert planning.bms_limited_rate_kw(50, 0) == 2.56


def test_bms_limited_rate_missing_is_none(planning):
    for bad in (None, 0, -5, "x", True):
        assert planning.bms_limited_rate_kw(bad, 51.2) is None


def test_resolve_uses_bms_in_place_of_bands(planning):
    assert planning.resolve_used_charge_rate(3.0, None, 8) == (2.1, 0.70)
    assert planning.resolve_used_charge_rate(3.0, None, 8, True, 2.6) == (2.6, 0.70)   # bms above the band guess
    assert planning.resolve_used_charge_rate(3.0, None, 20, True, 1.2) == (1.2, 1.0)   # bms below nameplate
    assert planning.resolve_used_charge_rate(3.0, 2.0, 8, True, 2.5) == (2.0, 0.70)    # learned rate still caps
    assert planning.resolve_used_charge_rate(3.0, 2.0, 8, False, 1.0) == (2.0, 0.70)   # measured keeps priority


def test_rate_source(planning):
    assert planning.charge_rate_source(True, True, True) == "measured"
    assert planning.charge_rate_source(False, True, True) == "bms"
    assert planning.charge_rate_source(False, False, True) == "bands"


def test_plan_fields_logged():
    import sys
    fields = sys.modules["sunsynk_optimizer.data_logger"]._IMPORT_PLAN_FIELDS
    for f in ("bms_charge_limit_a", "bms_rate_kw", "bms_rate_live", "rate_source", "bms_shadow_window_end"):
        assert f in fields
