"""Phase 48: grid outage helpers."""


def test_grid_online_parsing(planning):
    for v in ("Connected", "on", "1", "Grid Online", 1):
        assert planning.grid_online(v) is True
    for v in ("Disconnected", "off", "0", "offline", "Off-grid", 0):
        assert planning.grid_online(v) is False
    for v in (None, "unavailable", "unknown", "", "weird"):
        assert planning.grid_online(v) is None


def test_grid_on_grid_labels_and_fallback(planning):
    for v in ("On-grid", "on grid", "Grid-tied"):
        assert planning.grid_online(v) is True
    assert planning.grid_online("Off-grid") is False
    assert planning.grid_restored_fallback("weird", "Off-grid") is True
    assert planning.grid_restored_fallback("Off-grid", "Off-grid") is False
    assert planning.grid_restored_fallback("unavailable", "Off-grid") is False
    assert planning.grid_restored_fallback(None, "x") is False


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
