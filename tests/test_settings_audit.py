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


# ---- Phase 119: one-off settings dump ----

# ---- Phase 128: a settings-audit notification must mask identifiers too, like settings_dump ----

def test_redact_settings_changes_masks_a_value_equal_to_an_identifier(planning):
    changes = [
        {"key": "serverIp", "old": "2305123456", "new": "2305999999"},
        {"key": "mode", "old": "1", "new": "2"},
    ]
    out = planning.redact_settings_changes(changes, ("2305123456", "2305999999"))
    assert out[0] == {"key": "serverIp", "old": "**REDACTED**", "new": "**REDACTED**"}
    assert out[1] == {"key": "mode", "old": "1", "new": "2"}  # untouched: not an identifier value


def test_redact_settings_changes_is_a_no_op_without_identifiers_or_changes(planning):
    changes = [{"key": "a", "old": "1", "new": "2"}]
    assert planning.redact_settings_changes(changes, ()) == changes
    assert planning.redact_settings_changes([], ("123456",)) == []


def test_settings_dump_keeps_every_scalar_and_redacts_identifiers(planning):
    raw = {
        "sellTime1Pac": 3600, "cap1": "20", "sn": "2305123456", "id": 77, "apiKeyName": "abc",
        "plantName": "x", "comment": "2305123456", "nested": {"a": 1}, "empty": None, "long": "z" * 200,
    }
    out = planning.settings_dump(raw, ("2305123456", "", "12"))
    assert out["sellTime1Pac"] == "3600" and out["cap1"] == "20" and out["empty"] == ""
    assert out["sn"] == out["id"] == out["apiKeyName"] == out["comment"] == "**REDACTED**"
    assert "nested" not in out and len(out["long"]) == 80
    assert list(out) == sorted(out)
    assert planning.settings_dump("nope") == {}
