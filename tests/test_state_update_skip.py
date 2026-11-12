"""Tests for phase 67: update_state skips a write when nothing differs."""

from __future__ import annotations

from types import SimpleNamespace


def _state(**kw):
    return SimpleNamespace(a=1, b={"x": 1}, c=None, **kw)


def test_equal_values_are_not_a_change(planning):
    assert planning.state_fields_changed(_state(), {"a": 1, "b": {"x": 1}, "c": None}) is False


def test_different_value_is_a_change(planning):
    assert planning.state_fields_changed(_state(), {"a": 2}) is True
    assert planning.state_fields_changed(_state(), {"b": {"x": 2}}) is True
    assert planning.state_fields_changed(_state(), {"c": 0}) is True


def test_same_object_counts_as_changed_in_case_it_was_mutated(planning):
    st = _state()
    st.b["x"] = 2  # mutated in place, then passed back
    assert planning.state_fields_changed(st, {"b": st.b}) is True


def test_unknown_fields_are_ignored(planning):
    assert planning.state_fields_changed(_state(), {"nope": 1}) is False
    assert planning.state_fields_changed(_state(), {"nope": 1, "a": 2}) is True


# --------------------------------------------------------------------------- #
# Phase 92: fewer state writes                                                 #
# --------------------------------------------------------------------------- #


def test_clip_text_fits_the_entity_state_limit(planning):
    assert planning.clip_text("short") == "short"
    assert planning.clip_text("x" * 255) == "x" * 255
    clipped = planning.clip_text("x" * 900)
    assert len(clipped) == 255 and clipped.endswith("…")
    assert planning.clip_text("abcdefghij", 5) == "abcd…"
    assert planning.clip_text(None) is None and planning.clip_text(7) == 7


def test_update_state_persist_flag_is_only_used_for_derived_fields():
    import re
    from pathlib import Path
    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
    src = (pkg / "optimizer.py").read_text("utf-8")
    calls = re.findall(r"update_state\(([^()]*persist=False[^()]*)", src)
    fields = {name for call in calls for name in re.findall(r"(\w+)=", call)} - {"touch", "persist"}
    assert fields == {"battery_runtime", "import_causes", "marginal_cost"}
    coord = (pkg / "coordinator.py").read_text("utf-8")
    assert coord.index("if not persist:") < coord.index("self.storage.async_delay_save")
