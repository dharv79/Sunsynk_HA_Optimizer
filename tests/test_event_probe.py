"""Phase 43: event-route probe candidates."""


def test_paths_filled_and_read_only(planning):
    paths = planning.event_probe_paths("SN123", "42")
    assert len(paths) == len(planning.EVENT_PROBE_ROUTES) and len(set(paths)) == len(paths)
    assert all("{" not in p and p.startswith("/api/v1/") for p in paths)
    assert any("SN123" in p for p in paths) and any("/42/" in p for p in paths)
    assert all("lan=en" in p for p in paths)
    dated = planning.event_probe_paths("SN123", "42", "2026-11-04", "2026-10-05")
    assert any("2026-11-04" in p and "2026-10-05" in p for p in dated)


def test_normalise_inverter_events(planning):
    assert planning.normalise_inverter_events([]) == []  # healthy reply: empty list, not None
    assert planning.normalise_inverter_events(None) is None  # unreadable is not "no faults"
    assert planning.normalise_inverter_events({"x": 1}) is None
    events = planning.normalise_inverter_events(
        [{"eventName": "Battery over temperature", "code": "F58", "startTime": "2026-11-04 03:00:00"}, "odd"]
    )
    assert events[0]["category"] == "battery" and events[0]["code"] == "F58"
    assert events[1]["text"] == "odd" and events[1]["category"] == "other"


def test_new_inverter_events_dedup(planning):
    events = planning.normalise_inverter_events([{"name": "Grid fault", "code": "1"}])
    assert planning.new_inverter_events(events, []) == events
    assert planning.new_inverter_events(events, [events[0]["key"]]) == []
