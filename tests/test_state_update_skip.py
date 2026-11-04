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
