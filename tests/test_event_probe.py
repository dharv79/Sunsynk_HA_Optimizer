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


_NOTICES = [  # shape captured from the portal Notifications page, 04/11/2026 (newest first)
    {"id": "b", "sn": "2411132500", "messageType": 2, "createAt": "2026-10-09T17:05:37.000+00:00",
     "description": "The system detects that your plant (#{stationName})power grid has been restored."},
    {"id": "a", "sn": "2411132500", "messageType": 7, "createAt": "2026-10-09T17:04:32.000+00:00",
     "description": "The system detects that your plant (#{stationName}) disconnected from the grid."},
    {"id": "c", "sn": "2411132500", "messageType": 99, "createAt": "2026-10-10T00:00:00+00:00", "description": "other"},
    {"id": "d", "sn": "OTHER", "messageType": 7, "createAt": "2026-10-11T00:00:00+00:00"},
]


def test_normalise_grid_notices(planning):
    assert planning.normalise_grid_notices(None) is None
    notices = planning.normalise_grid_notices(_NOTICES, "2411132500")
    assert [(n["id"], n["kind"]) for n in notices] == [("a", "lost"), ("b", "restored")]
    # description fallback when messageType is unknown
    text_only = [{"id": "x", "description": "...disconnected from the grid."}]
    assert planning.normalise_grid_notices(text_only)[0]["kind"] == "lost"
    assert [n["id"] for n in planning.new_grid_notices(notices, ["a"])] == ["b"]


def test_inverter_counts_and_fault_flag(planning):
    assert planning.normalise_inverter_counts(None) is None
    counts = planning.normalise_inverter_counts(
        {"warning": 0, "fault": 0, "total": 1, "normal": 1, "offline": 0, "protect": 0}
    )
    assert counts["fault"] == 0 and counts["protect"] == 0
    assert planning.normalise_inverter_counts({"fault": "x"}) is None  # nothing readable, never 0
    assert planning.inverter_fault_flag([], counts) is False
    assert planning.inverter_fault_flag(None, {**counts, "protect": 1}) is True
    assert planning.inverter_fault_flag([{"text": "F58"}], counts) is True
    assert planning.inverter_fault_flag(None, None) is None
