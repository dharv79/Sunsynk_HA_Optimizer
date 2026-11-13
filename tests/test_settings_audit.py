"""Phase 107: inverter settings snapshot and change audit (pure)."""


def test_snapshot_drops_ignored_and_non_scalar(planning):
    snap = planning.snapshot_settings({
        "sdBatteryCurrent": 40, "sn": "X1", "updateTime": "t", "batteryCap": 100,
        "nested": {"a": 1}, "mode": "Zero export", "blank": None, "flag": True,
    })
    assert snap == {"batteryCap": "100", "mode": "Zero export", "blank": "", "flag": "True"}
    assert planning.snapshot_settings(None) == {}


def test_first_snapshot_has_no_changes(planning):
    assert planning.settings_changes(None, {"a": "1"}) == []
    assert planning.settings_changes({}, {"a": "1"}) == []


def test_changes_added_removed_and_numeric_text(planning):
    prev = {"a": "5", "b": "x", "c": "1", "gone": "9"}
    cur = {"a": "5.0", "b": "y", "c": "1", "new": "2"}
    assert planning.settings_changes(prev, cur) == [
        {"key": "b", "old": "x", "new": "y"},
        {"key": "gone", "old": "9", "new": None},
        {"key": "new", "old": None, "new": "2"},
    ]


def test_text_lines_and_limit(planning):
    changes = [{"key": f"k{i}", "old": "1", "new": "2"} for i in range(15)]
    text = planning.settings_change_text(changes)
    assert text.startswith("k0: 1 → 2") and text.endswith("(+3 more)")
    assert planning.settings_change_text([]) is None
    assert "(none) → 2" in planning.settings_change_text([{"key": "n", "old": None, "new": "2"}])
