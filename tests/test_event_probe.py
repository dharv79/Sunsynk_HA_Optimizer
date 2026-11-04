"""Phase 43: event-route probe candidates."""


def test_paths_filled_and_read_only(planning):
    paths = planning.event_probe_paths("SN123", "42")
    assert len(paths) == len(planning.EVENT_PROBE_ROUTES) and len(set(paths)) == len(paths)
    assert all("{" not in p and p.startswith("/api/v1/") for p in paths)
    assert any("SN123" in p for p in paths) and any("/42/" in p for p in paths)
    assert all("lan=en" in p for p in paths)
