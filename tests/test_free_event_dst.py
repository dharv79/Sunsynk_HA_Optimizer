"""Tests for phase 51: DST-safe free-event timing, the watch_from guard and the sold-energy split."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def test_elapsed_seconds_is_real_time_across_clocks_back(planning):
    # 25 Oct 2026 00:00 BST -> 09:00 GMT is 10 real hours (wall clock says 9).
    a = datetime(2026, 10, 25, 0, 0, tzinfo=LONDON)
    b = datetime(2026, 10, 25, 9, 0, tzinfo=LONDON)
    assert planning.elapsed_seconds(b, a) == 10 * 3600


def test_elapsed_seconds_is_real_time_across_clocks_forward(planning):
    # 29 Mar 2026: 00:30 GMT -> 04:30 BST is 3 real hours (wall clock says 4).
    a = datetime(2026, 3, 29, 0, 30, tzinfo=LONDON)
    b = datetime(2026, 3, 29, 4, 30, tzinfo=LONDON)
    assert planning.elapsed_seconds(b, a) == 3 * 3600


def test_watch_from_is_six_real_hours_before_free_start_over_dst(planning):
    free_start = datetime(2026, 10, 25, 9, 0, tzinfo=LONDON)
    watch = planning.free_event_watch_from(free_start)
    assert planning.elapsed_seconds(free_start, watch) == planning.FREE_EVENT_MAX_SELL_HOURS * 3600
    assert watch.utcoffset() is not None
    # Armed at 23:00 BST on the 24th, the watch is 6 real hours before the free start.
    now = datetime(2026, 10, 24, 23, 0, tzinfo=LONDON)
    assert planning.elapsed_seconds(watch, now) == (planning.elapsed_seconds(free_start, now)
                                                    - planning.FREE_EVENT_MAX_SELL_HOURS * 3600)


def test_watch_from_naive_unchanged(planning):
    assert planning.free_event_watch_from(datetime(2026, 10, 18, 11, 0)) == datetime(2026, 10, 18, 7, 0)


def test_in_watch_guard_blocks_a_sell_before_watch_from(planning):
    free_start = datetime(2026, 10, 25, 9, 0, tzinfo=LONDON)
    watch = planning.free_event_watch_from(free_start)
    before = planning.shift_hours(watch, -0.25)
    assert not planning.free_event_in_watch(before, free_start, watch)
    assert not planning.free_event_in_watch(before, free_start)
    assert planning.free_event_in_watch(watch, free_start, watch)
    assert planning.free_event_in_watch(planning.shift_hours(free_start, -0.5), free_start, watch)
    assert not planning.free_event_in_watch(free_start, free_start, watch)


def _sold_event(**kw):
    ev = {"soc_at_sell_start": 90.0, "soc_at_free_start": 40.0,
          "meters_at_sell_start": {"grid_export": 10.0}, "meters_at_free_start": {"grid_export": 20.7}}
    ev.update(kw)
    return ev


def test_sold_energy_is_split_into_battery_and_solar(planning):
    r = planning.free_event_result(_sold_event(), {"grid_import": 5.0}, 100.0, 27.0, 10.0)
    assert r["sold_kwh"] == 10.7
    assert r["sold_from_battery_kwh"] == 5.0
    assert r["sold_from_solar_kwh"] == 5.7


def test_split_is_none_without_capacity_or_soc(planning):
    r = planning.free_event_result(_sold_event(), {"grid_import": 5.0}, 100.0, 27.0)
    assert r["sold_from_battery_kwh"] is None and r["sold_from_solar_kwh"] is None
    r = planning.free_event_result(_sold_event(soc_at_free_start=None), {"grid_import": 5.0}, 100.0, 27.0, 10.0)
    assert r["sold_from_battery_kwh"] is None and r["sold_from_solar_kwh"] is None


def test_split_is_none_for_solar_when_meters_missing(planning):
    r = planning.free_event_result(_sold_event(meters_at_free_start=None), None, 100.0, 27.0, 10.0)
    assert r["sold_kwh"] is None
    assert r["sold_from_battery_kwh"] == 5.0 and r["sold_from_solar_kwh"] is None


def test_no_sell_means_zero_split(planning):
    r = planning.free_event_result({"meters_at_free_start": {"grid_import": 1.0}}, {"grid_import": 3.0}, 100.0, 27.0, 10.0)
    assert r["sold_kwh"] == 0.0 and r["sold_from_battery_kwh"] == 0.0 and r["sold_from_solar_kwh"] == 0.0
