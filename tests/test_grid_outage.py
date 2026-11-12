"""Phase 48: grid outage helpers."""


def test_grid_online_from_voltage(planning):
    assert planning.grid_online_from_voltage(240.4) is True
    assert planning.grid_online_from_voltage("231") is True
    assert planning.grid_online_from_voltage(0) is False
    assert planning.grid_online_from_voltage(12) is False
    for v in (None, "unavailable", 140):
        assert planning.grid_online_from_voltage(v) is None


def test_runtime(planning):
    assert planning.outage_runtime_hours(60, 20, 10.0, 0.8) == 5.0
    assert planning.outage_runtime_hours(15, 20, 10.0, 0.8) == 0.0
    assert planning.outage_runtime_hours(None, 20, 10.0, 0.8) is None
    assert planning.outage_runtime_hours(60, 20, 10.0, None) is None
    assert planning.outage_runtime_hours(60, 20, 10.0, 0.0) is None


def test_track_and_record(planning):
    o = {"start": "2026-11-04T10:00:00+00:00", "soc_start": 80, "soc_low": None}
    o = planning.outage_track(o, 70)
    o = planning.outage_track(o, 75)
    o = planning.outage_track(o, None)
    assert o["soc_low"] == 70
    rec = planning.outage_record(o, "2026-11-04T11:30:00+00:00", 72, 10.0)
    assert rec["minutes"] == 90 and rec["battery_kwh_served"] == 1.0 and rec["soc_end"] == 72


def test_record_unknown_soc_is_none(planning):
    rec = planning.outage_record({"start": "bad", "soc_start": None}, "2026-11-04T11:30:00+00:00", None, 10.0)
    assert rec["battery_kwh_served"] is None and rec["minutes"] is None
