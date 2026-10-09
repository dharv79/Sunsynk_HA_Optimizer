"""Tests for phase 21: Octopus Saving Sessions planner, validation and auto-detect parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

UTC = timezone.utc
START = datetime(2026, 10, 12, 17, 0, tzinfo=UTC)
END = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def _plan(planning, **overrides):
    args = dict(
        session_start=START, session_end=END, soc=60.0, capacity_kwh=10.0,
        charge_rate_kw=3.0, export_rate_kw=5.0, reserve_soc=45,
        reward_pence=225.0, export_pence=27.81, offpeak_pence=16.66, day_pence=27.77,
    )
    args.update(overrides)
    return planning.plan_saving_session(**args)


def test_high_reward_exports_to_minimum_and_fills_first(planning):
    plan = _plan(planning)
    assert plan.floor_soc == planning.SAVING_SESSION_MIN_FLOOR_SOC
    assert plan.floor_reason == "reward_beats_rebuy"
    assert plan.value_pence == 252.81
    assert plan.offpeak_boost and plan.precharge
    # 40% of 10 kWh at 3 kW = 80 min, ending at 16:00 (session starts inside the peak).
    assert plan.precharge_end == START.replace(hour=16)
    assert plan.precharge_start == plan.precharge_end - timedelta(minutes=80)
    # Full battery down to 20% = 8 kWh, capped by 5 kW x 1 h.
    assert plan.expected_export_kwh == 5.0


def test_precharge_before_peak_ends_at_session_start(planning):
    start = START.replace(hour=13)
    plan = _plan(planning, session_start=start, session_end=start + timedelta(hours=1))
    assert plan.precharge_end == start


def test_low_value_keeps_evening_reserve_and_skips_day_top_up(planning):
    plan = _plan(planning, reward_pence=None, export_pence=19.0)
    assert plan.floor_soc == 45 and plan.floor_reason == "evening_reserve"
    assert not plan.precharge and plan.precharge_start is None
    # 19p doesn't beat 16.66p off-peak after losses (19.6p), so no boost either.
    assert not plan.offpeak_boost
    assert plan.expected_export_kwh == round((60 - 45) / 100 * 10, 2)


def test_missing_prices_never_count_as_worth_it(planning):
    plan = _plan(planning, reward_pence=None, export_pence=None)
    assert plan.value_pence is None
    assert plan.floor_reason == "evening_reserve"
    assert not plan.offpeak_boost and not plan.precharge
    plan = _plan(planning, day_pence=None, offpeak_pence=None)
    assert plan.floor_reason == "evening_reserve" and not plan.precharge and not plan.offpeak_boost


def test_full_battery_needs_no_top_up(planning):
    plan = _plan(planning, soc=100.0)
    assert not plan.precharge and plan.offpeak_boost
    assert plan.expected_export_kwh == 5.0


def test_reserve_floor_is_clamped(planning):
    assert _plan(planning, reward_pence=None, export_pence=None, reserve_soc=10).floor_soc == 20
    assert _plan(planning, reward_pence=None, export_pence=None, reserve_soc=120).floor_soc == 100


def test_octopoints_conversion(planning):
    assert planning.octopoints_to_pence(1800) == 225.0
    assert planning.octopoints_to_pence(None) is None
    assert planning.octopoints_to_pence("x") is None
    assert planning.octopoints_to_pence(0) is None


def test_manual_validation_uses_label(planning):
    err = planning.manual_free_event_error("start", NOW - timedelta(hours=1), END, NOW, "Saving session")
    assert err == "Saving session start must be in the future."
    assert planning.manual_free_event_error("end", START, START, NOW, "Saving session") == "Saving session end must be after start."
    assert planning.manual_free_event_error("end", START, END, NOW, "Saving session") is None
    # Default label unchanged for the free event.
    assert planning.manual_free_event_error("start", NOW, END, NOW).startswith("Free event")


def test_next_joined_session_picks_earliest_future(planning):
    attrs = {
        "available_events": [{"id": 9, "start": "2026-10-12T15:00:00+00:00", "end": "2026-10-12T16:00:00+00:00"}],
        "joined_events": [
            {"id": 1, "start": "2026-10-11T17:00:00+00:00", "end": "2026-10-11T18:00:00+00:00", "octopoints_per_kwh": 1800},
            {"id": 3, "start": "2026-10-13T17:30:00Z", "end": "2026-10-13T18:30:00Z", "octopoints_per_kwh": 2400},
            {"id": 2, "start": START, "end": END, "octopoints_per_kwh": 1800},
        ],
    }
    assert planning.next_joined_saving_session(attrs, NOW) == (START, END, 225.0)


def test_next_joined_session_skips_running_and_malformed(planning):
    attrs = {
        "joined_events": [
            {"start": "2026-10-12T08:30:00+00:00", "end": "2026-10-12T09:30:00+00:00"},  # under way
            {"start": "garbage", "end": "2026-10-12T18:00:00+00:00"},
            {"start": "2026-10-12T18:00:00+00:00", "end": "2026-10-12T17:00:00+00:00"},  # end before start
            "not a dict",
        ]
    }
    assert planning.next_joined_saving_session(attrs, NOW) is None
    assert planning.next_joined_saving_session(None, NOW) is None
    assert planning.next_joined_saving_session({"joined_events": "x"}, NOW) is None
    # Unknown reward → None, never 0p.
    one = {"joined_events": [{"start": "2026-10-12T17:00:00", "end": "2026-10-12T18:00:00"}]}
    assert planning.next_joined_saving_session(one, NOW) == (START, END, None)
