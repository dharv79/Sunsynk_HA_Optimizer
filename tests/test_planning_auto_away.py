"""Tests for phase 27: automatic away-mode decision."""

from __future__ import annotations


def test_anyone_home_means_home(planning):
    assert planning.auto_away([False, True], True, 24, [50, 1]) is False


def test_all_away_long_enough(planning):
    assert planning.auto_away([False, False], None, 24, [30, 48]) is True


def test_all_away_but_not_long_enough_leaves_switch(planning):
    assert planning.auto_away([False, False], None, 24, [30, 5]) is None


def test_unavailable_person_is_ignored_not_home(planning):
    assert planning.auto_away([None, False], None, 24, [None, 30]) is True
    assert planning.auto_away([None], None, 24, [None]) is None


def test_calendar_only(planning):
    assert planning.auto_away([], True) is True
    assert planning.auto_away([], False) is False
    assert planning.auto_away([], None) is None


def test_calendar_event_overrides_short_absence(planning):
    assert planning.auto_away([False], True, 24, [2]) is True


def test_no_signal(planning):
    assert planning.auto_away([], None) is None
