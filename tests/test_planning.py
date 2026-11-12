"""Tests for the pure import-plan maths in planning.py."""

from __future__ import annotations

from datetime import datetime

import pytest


def _select(planning, **overrides):
    kwargs = dict(
        is_full_day=False,
        low_solar_forecast_kwh=15.0,
        band="summer_like",
        hours_to_solar=2.0,
        avg_consumption_kw=0.5,
        battery_capacity_kwh=10.0,
    )
    kwargs.update(overrides)
    return planning.select_target_soc(**kwargs)


@pytest.mark.parametrize("kwh,band", [(10, "summer_like"), (9.99, "shoulder"), (5.01, "shoulder"), (5, "winter_like")])
def test_forecast_band_edges(planning, kwh, band):
    assert planning.forecast_band(kwh) == band


def test_bridge_soc_clamps(planning):
    assert planning.bridge_soc(0, 10) == 30
    assert planning.bridge_soc(1, 10) == 30      # 20 + 10
    assert planning.bridge_soc(2, 10) == 40
    assert planning.bridge_soc(50, 10) == 100


def test_walk_bridge_gap_stops_at_first_covering_hour(planning):
    hourly = {5: 0.0, 6: 0.2, 7: 0.6, 8: 2.0}
    gap, hour = planning.walk_bridge_gap(hourly, 0.5)
    assert hour == 7
    assert gap == pytest.approx(0.5 + 0.3)
    # Scale applies before the comparison.
    gap, hour = planning.walk_bridge_gap(hourly, 0.5, scale=3.0)
    assert hour == 6
    assert gap == pytest.approx(0.5)


def test_walk_bridge_gap_never_covered(planning):
    gap, hour = planning.walk_bridge_gap({}, 0.5)
    assert hour is None
    assert gap == pytest.approx(0.5 * 8)  # 05:00-12:00 inclusive


def test_synthetic_profile_sums_to_daily_total(planning):
    profile = planning.synthetic_hourly_profile(20.0, 6.0, 20.0)
    assert sum(profile.values()) == pytest.approx(20.0)
    assert profile[3] == 0.0 and profile[22] == 0.0
    assert max(profile, key=profile.get) in (12, 13)
    assert planning.synthetic_hourly_profile(0, 6, 20) is None
    assert planning.synthetic_hourly_profile(10, 20, 6) is None


def test_select_low_solar_override(planning):
    d = _select(planning, low_solar_forecast_kwh=6.9, band="shoulder")
    assert (d.target_soc, d.reason) == (95, "low_solar_override")
    d = _select(planning, low_solar_forecast_kwh=3, band="winter_like")
    assert (d.target_soc, d.reason) == (100, "low_solar_override_winter_like")


def test_select_full_day(planning):
    d = _select(planning, is_full_day=True)
    assert (d.target_soc, d.reason) == (30, "weekly_full_charge_day")  # 20 + 1kWh/10 → 30
    assert _select(planning, is_full_day=True, low_solar_forecast_kwh=5).target_soc == 100
    assert _select(planning, is_full_day=True, hours_to_solar=None).target_soc == 100


def test_select_hourly_bridge_wins_over_simple(planning):
    d = _select(planning, hourly_forecast_kwh={5: 0.0, 6: 0.0, 7: 1.0}, hourly_correction=1.0)
    assert d.reason == "solar_bridge_hourly"
    assert d.hourly_forecast_used and d.bridge_hour == 7
    assert d.target_soc == 30  # gap 1.0 kWh


def test_select_synthetic_ramp_only_raises(planning):
    slow = {h: 0.0 for h in range(5, 13)}
    d = _select(planning, hours_to_solar=1.0, avg_consumption_kw=1.0, synthetic_profile=slow)
    assert d.reason == "solar_bridge_ramp" and d.synthetic_ramp
    assert d.target_soc == 100  # 8 kWh gap on 10 kWh battery
    fast = {5: 5.0}
    d = _select(planning, hours_to_solar=3.0, avg_consumption_kw=1.0, synthetic_profile=fast)
    assert d.reason == "solar_bridge" and not d.synthetic_ramp
    assert d.target_soc == 57 and d.bridge_hour == 5  # 3 kWh x 1.25 margin


@pytest.mark.parametrize("band,target", [("winter_like", 95), ("summer_like", 80), ("shoulder", 85)])
def test_select_band_fallback_without_sun(planning, band, target):
    d = _select(planning, hours_to_solar=None, band=band)
    assert (d.target_soc, d.reason) == (target, band)


def test_apply_soc_adjustments_keeps_morning_floor(planning):
    assert planning.apply_soc_adjustments(40, 10, 0) == 50
    assert planning.apply_soc_adjustments(30, 15, -5) == 40   # 40 - 15 = 25, ok
    assert planning.apply_soc_adjustments(20, 10, -5) == 35   # 25 → est 15 < 25 → 25 + 10
    assert planning.apply_soc_adjustments(95, 15, 5) == 100


@pytest.mark.parametrize("temp,factor", [(20, 1.0), (15, 0.85), (10, 0.70), (5, 0.55)])
def test_temp_deration(planning, temp, factor):
    assert planning.temp_deration_factor(temp) == factor


def test_resolve_used_charge_rate(planning):
    assert planning.resolve_used_charge_rate(3.0, None, 20) == (3.0, 1.0)
    assert planning.resolve_used_charge_rate(3.0, 2.8, 20) == (3.0, 1.0)   # within 10% → nameplate
    assert planning.resolve_used_charge_rate(3.0, 2.0, 20) == (2.0, 1.0)
    assert planning.resolve_used_charge_rate(3.0, 2.0, 8) == (1.4, 0.70)


def test_flux1_end_minutes(planning):
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(0.0, 3.0)) == "02:15"
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(1.6, 3.0)) == "02:45"  # 32 min → 45
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(3.0, 3.0)) == "03:00"
    assert planning.minutes_to_hhmm(planning.flux1_end_minutes(30.0, 3.0)) == "05:00"


def test_charge_efficiency_only_on_nameplate_rate(planning):
    assert planning.charge_efficiency(3.0, None) == planning.CHARGE_EFFICIENCY
    assert planning.charge_efficiency(3.0, 2.8) == planning.CHARGE_EFFICIENCY  # nameplate used
    assert planning.charge_efficiency(3.0, 2.0) == 1.0  # learned rate already embeds losses


def test_flux1_sizing_unit_efficiency_and_no_load_matches_old(planning):
    for kwh in (0.0, 1.6, 3.0, 30.0):
        end = planning.flux1_end_minutes(kwh / 1.0, 3.0)
        assert end == planning.flux1_end_minutes(kwh, 3.0)
        assert planning.window_grid_kwh(kwh, 1.0, 0.0, end) == (0.0, round(kwh, 2))


def test_flux1_sizing_losses_lengthen_window_and_clamp_holds(planning):
    # 2.9 kWh at 3 kW: 58 min → 03:00 without losses; 63 min → 03:15 with 0.92.
    assert planning.flux1_end_minutes(2.9, 3.0) == 3 * 60
    assert planning.flux1_end_minutes(2.9 / 0.92, 3.0) == 3 * 60 + 15
    assert planning.flux1_end_minutes(30.0 / 0.92, 3.0) == 5 * 60
    assert planning.flux1_end_minutes(0.0 / 0.92, 3.0) == 2 * 60 + 15


def test_window_grid_kwh_adds_load_over_window(planning):
    # 75 min window at 0.8 kW = 1.0 kWh load; 4.6 kWh / 0.92 = 5.0 kWh stored need.
    assert planning.window_grid_kwh(4.6, 0.92, 0.8, 3 * 60 + 15) == (1.0, 6.0)
    assert planning.window_grid_kwh(0.0, 0.92, 0.6, 5 * 60) == (1.8, 1.8)


def test_score_full_charge_day(planning):
    sunny = {"condition": "sunny", "cloud_coverage": 10, "precipitation_probability": 0, "temperature": 20}
    assert planning.score_full_charge_day(sunny, "Monday") == 118.0
    assert planning.score_full_charge_day(sunny, "Friday") == 103.0
    rainy = {"condition": "rainy", "cloud_coverage": 90, "precipitation_probability": 80, "temperature": 4}
    assert planning.score_full_charge_day(rainy, "Tuesday") == -74.0


def test_net_cost_and_sum_field(planning):
    assert planning.net_cost_gbp(2.0, 0.5, 1.0) == 2.5
    assert planning.net_cost_gbp(2.0, None, 1.0) is None
    days = [{"x": 1.111}, {"x": None}, {}, {"x": 2.0}]
    assert planning.sum_field(days, "x") == 3.11
    assert planning.sum_field([{}], "x") is None


# --------------------------------------------------------------------------- #
# Weekly digest period                                                        #
# --------------------------------------------------------------------------- #

def test_trailing_week_is_seven_days_ending_yesterday(planning):
    from datetime import date

    # Sunday 2026-09-20 digest covers Sun 13th .. Sat 19th.
    assert planning.trailing_week(date(2026, 9, 20)) == ("2026-09-13", "2026-09-19")


def test_days_in_period_keeps_full_week_and_drops_today(planning):
    days = [{"date": f"2026-09-{d:02d}"} for d in range(12, 21)]  # 12th..20th
    kept = planning.days_in_period(days, "2026-09-13", "2026-09-19")
    assert [d["date"] for d in kept] == [f"2026-09-{d:02d}" for d in range(13, 20)]


def test_week_history_covers_oldest_plan_record(planning):
    # Oldest day's 01:55 import_plan is ~7 d 16 h before the Sunday 18:00 digest.
    assert planning.WEEK_HISTORY_DAYS * 24 > 7 * 24 + 16


# --------------------------------------------------------------------------- #
# Free electricity event (phase 10)                                           #
# --------------------------------------------------------------------------- #

def _free(planning, **overrides):
    kwargs = dict(
        free_start=datetime(2026, 9, 28, 13, 0),
        free_end=datetime(2026, 9, 28, 15, 0),
        soc=90.0,
        capacity_kwh=10.0,
        charge_rate_kw=2.0,
        export_rate_kw=2.0,
    )
    kwargs.update(overrides)
    return planning.plan_free_event(**kwargs)


def test_free_event_floor_maths(planning):
    # 2h @ 2kW refill = 4kWh = 40% of 10kWh capacity -> floor = 100 - 40 = 60.
    plan = _free(planning)
    assert plan.sell_floor_soc == 60


def test_free_event_floor_clamped_at_40(planning):
    # 4h @ 3kW refill = 12kWh, more than capacity -> floor would go below 40, clamped.
    plan = _free(planning, free_end=datetime(2026, 9, 28, 17, 0), charge_rate_kw=3.0)
    assert plan.sell_floor_soc == planning.FREE_EVENT_MIN_FLOOR_SOC == 40


def test_free_event_sell_window_length(planning):
    # Floor 60, soc 90 -> sell 3kWh at 2kW export = 1.5h before free_start.
    plan = _free(planning)
    assert plan.sell_start == datetime(2026, 9, 28, 11, 30)
    assert plan.sell_end == datetime(2026, 9, 28, 13, 0)
    assert plan.expected_export_kwh == pytest.approx(3.0)
    assert plan.skip_sell is False


def test_free_event_sell_window_not_capped_at_free_period_length(planning):
    # Floor 60, soc 100, 1kW export -> 4kWh needs 4h; the 2h free period no longer caps it.
    plan = _free(planning, soc=100.0, export_rate_kw=1.0)
    assert plan.sell_start == datetime(2026, 9, 28, 9, 0)
    assert plan.expected_export_kwh == pytest.approx(4.0)
    assert plan.expected_refill_kwh == pytest.approx(4.0)


def test_free_event_sell_window_capped_at_max_sell_hours(planning):
    # Floor 60, soc 100, 0.5kW export -> 4kWh needs 8h, capped at 4h (2kWh).
    plan = _free(planning, soc=100.0, export_rate_kw=0.5)
    assert planning.FREE_EVENT_MAX_SELL_HOURS == 4.0
    assert plan.sell_start == datetime(2026, 9, 28, 9, 0)
    assert plan.expected_export_kwh == pytest.approx(2.0)


def test_free_event_watch_from(planning):
    assert planning.free_event_watch_from(datetime(2026, 10, 18, 11, 0)) == datetime(2026, 10, 18, 7, 0)


def test_free_event_sell_status_and_attributes(planning):
    # The 16/10 event: entered at 40% SOC, so the scheduling estimate said "sell 0 kWh at 11:00".
    event = {
        "phase": "scheduled",
        "free_start": "2026-10-18T11:00:00+01:00",
        "sell_start": "2026-10-18T11:00:00+01:00",
        "sell_end": "2026-10-18T11:00:00+01:00",
        "skip_sell": True,
        "expected_export_kwh": 0.0,
        "sell_floor_soc": 40,
        "meters_at_free_start": {"grid_import": 1.0},
    }
    attrs = planning.free_event_attributes(event)
    assert attrs["sell_status"] == "Watching from Sun 07:00"
    assert not {"sell_start", "sell_end", "skip_sell"} & attrs.keys()
    assert attrs["expected_export_kwh"] is None  # kept as a key for the dashboard (Spook)
    assert "meters_at_free_start" not in attrs and attrs["sell_floor_soc"] == 40
    # No event at all: every dashboard attribute is still present.
    assert set(planning.FREE_EVENT_ATTRIBUTE_KEYS) <= planning.free_event_attributes({}).keys()

    sold = {**event, "phase": "selling", "sell_start": "2026-10-18T08:40:00+01:00", "skip_sell": False,
            "expected_export_kwh": 3.5, "soc_at_sell_start": 75.0}
    assert planning.free_event_sell_status(sold) == "Selling since 08:40"
    assert planning.free_event_attributes(sold)["expected_export_kwh"] == 3.5
    assert planning.free_event_sell_status({**sold, "phase": "done"}) == "Sold from 08:40"
    assert planning.free_event_sell_status({**event, "phase": "charging"}) == "Not needed"
    assert planning.free_event_sell_status({**event, "phase": "cancelled"}) == "Cancelled"
    assert planning.free_event_sell_status({}) is None


def test_soc_trend_kw(planning):
    from datetime import timedelta

    now = datetime(2026, 10, 18, 9, 0)
    samples = [(now - timedelta(minutes=m), soc) for m, soc in ((40, 50.0), (20, 60.0), (10, 62.0), (0, 65.0))]
    # Only the last 30 min count: 60% -> 65% over 20 min on 10kWh = 1.5 kW.
    assert planning.soc_trend_kw(samples, 10.0, now) == 1.5
    assert planning.soc_trend_kw([(now, 65.0)], 10.0, now) is None  # one sample
    assert planning.soc_trend_kw([(now - timedelta(minutes=2), 64.0), (now, 65.0)], 10.0, now) is None  # too short
    assert planning.soc_trend_kw([(now - timedelta(minutes=20), 70.0), (now, 65.0)], 10.0, now) == -1.5


def test_free_event_sell_decision_18_oct(planning):
    # 10kWh, floor 40, export 1.5kW, free start 11:00.
    decide = planning.free_event_sell_decision
    # 07:00, SOC 60, no trend yet: 2kWh needs 1.33h + 0.25h < 4h left -> wait.
    assert decide(60.0, 40, 10.0, None, 1.5, 4.0)["sell_now"] is False
    # 08:00, SOC 70, solar charging at 1.5kW: projects 100% by 11:00 -> 6kWh needs 4h >= 3h left -> sell.
    d = decide(70.0, 40, 10.0, 1.5, 1.5, 3.0)
    assert d["sell_now"] is True and d["projected_soc"] == 100.0 and d["energy_kwh"] == 6.0 and d["needed_hours"] == 4.0
    # 09:30, SOC 62 and falling (house load): 2.2kWh needs 1.47h + 0.25h >= 1.5h left -> sell.
    assert decide(62.0, 40, 10.0, -0.4, 1.5, 1.5)["sell_now"] is True
    # Already at the floor with nothing coming in: never sells.
    assert decide(40.0, 40, 10.0, 0.0, 1.5, 0.5)["sell_now"] is False


def test_free_event_target_cap(planning):
    from datetime import date

    event = {"phase": "scheduled", "sell_floor_soc": 40}
    start = datetime(2026, 10, 18, 11, 0)
    day = date(2026, 10, 18)
    assert planning.free_event_target_cap(60, event, start, day) == 40
    assert planning.free_event_target_cap(30, event, start, day) == 30
    assert planning.free_event_target_cap(60, event, start, date(2026, 10, 17)) == 60  # not the event day
    assert planning.free_event_target_cap(60, event, datetime(2026, 10, 18, 16, 0), day) == 60  # peak or later
    assert planning.free_event_target_cap(60, {**event, "phase": "selling"}, start, day) == 60
    assert planning.free_event_target_cap(60, {}, None, day) == 60


def test_free_event_target_cap_covers_load_until_free_start(planning):
    # 18/10: capped at the bare 40% floor, the battery drained to its 25% minimum by
    # 06:50 and the house imported at the day rate until the 11:00 free start.
    from datetime import date

    event = {"phase": "scheduled", "sell_floor_soc": 40}
    start = datetime(2026, 10, 18, 11, 0)
    day = date(2026, 10, 18)
    # 05:00 -> 11:00 = 6h at 0.45kW = 2.7kWh of a 10kWh battery -> +27%.
    assert planning.free_event_target_cap(100, event, start, day, 0.45, 10.0) == 67
    assert planning.free_event_target_cap(60, event, start, day, 0.45, 10.0) == 60  # normal plan already lower
    assert planning.free_event_target_cap(100, event, datetime(2026, 10, 18, 4, 0), day, 0.45, 10.0) == 40
    assert planning.free_event_target_cap(100, event, datetime(2026, 10, 18, 15, 0), day, 1.0, 10.0) == 100
    assert planning.free_event_target_cap(100, event, start, day, None, 10.0) == 40  # no load: floor only


def test_free_event_skip_sell_when_already_at_or_below_floor(planning):
    plan = _free(planning, soc=55.0)  # below the 60% floor
    assert plan.skip_sell is True
    assert plan.expected_export_kwh == 0.0
    assert plan.sell_start == plan.sell_end == datetime(2026, 9, 28, 13, 0)


def test_free_event_expected_refill_capped_at_headroom(planning):
    # Floor 60, soc 95 -> sells 3.5kWh down to 60%, leaving 4kWh headroom to 100%,
    # exactly matching the 2h@2kW refill capacity (no cap needed here).
    plan = _free(planning, soc=95.0)
    assert plan.skip_sell is False
    assert plan.expected_export_kwh == pytest.approx(3.5)
    assert plan.expected_refill_kwh == pytest.approx(4.0)


def test_free_event_refill_capped_by_headroom_when_sell_was_capped(planning):
    # Export (0.5kW) too slow to reach the floor within the 4h cap: SOC at the free
    # start stays above the floor -> less headroom than the raw charge_rate*free_hours.
    plan = _free(planning, soc=100.0, charge_rate_kw=2.0, export_rate_kw=0.5)
    assert plan.expected_export_kwh == pytest.approx(2.0)  # 4h @ 0.5kW, not the 4kWh needed
    assert plan.expected_refill_kwh == pytest.approx(2.0)  # headroom 2kWh, not the raw 4kWh


def test_free_event_crosses_midnight(planning):
    plan = _free(
        planning,
        free_start=datetime(2026, 9, 28, 23, 0),
        free_end=datetime(2026, 9, 29, 1, 0),
        soc=90.0,
    )
    # Same 2h/60%-floor shape as the same-day case, just spanning midnight.
    assert plan.sell_floor_soc == 60
    assert plan.sell_start == datetime(2026, 9, 28, 21, 30)
    assert plan.sell_end == datetime(2026, 9, 28, 23, 0)


def test_free_event_overlapping_peak_window_is_plain_time_arithmetic(planning):
    # A free period starting inside 16:00-19:00: the planner has no special-case
    # for this — "event wins" over the peak window is an execution-level decision
    # (optimizer.py), not planning maths. This just checks the times still resolve.
    plan = _free(
        planning,
        free_start=datetime(2026, 9, 28, 17, 0),
        free_end=datetime(2026, 9, 28, 19, 0),
        soc=90.0,
    )
    assert plan.sell_floor_soc == 60
    assert plan.sell_start == datetime(2026, 9, 28, 15, 30)


def test_manual_free_event_error_only_judges_changed_field(planning):
    from datetime import timedelta

    manual_free_event_error = planning.manual_free_event_error

    now = datetime(2026, 9, 28, 22, 0)
    soon, later, past = now + timedelta(hours=1), now + timedelta(hours=2), now - timedelta(hours=1)
    assert manual_free_event_error("start", soon, later, now) is None
    assert manual_free_event_error("start", soon, past, now) is None  # stale end: wait
    assert manual_free_event_error("start", past, later, now) is not None
    assert manual_free_event_error("end", soon, later, now) is None
    assert manual_free_event_error("end", later, soon, now) is not None


@pytest.mark.parametrize(
    "source,hhmm,expected",
    [
        ("automatic", (1, 55), True),
        ("startup", (0, 30), False),
        ("startup", (1, 54), False),
        ("startup", (1, 55), True),
        ("startup", (14, 0), True),
    ],
)
def test_should_log_import_plan_skips_pre_nightly_startup(planning, source, hhmm, expected):
    now = datetime(2026, 10, 2, *hhmm)
    assert planning.should_log_import_plan(source, now) is expected


def test_daily_report_plans_keeps_nightly_and_appends_later_replan(planning):
    nightly = {"date": "2026-10-02", "soc": 47.0, "source": "automatic"}
    replan = {"date": "2026-10-02", "soc": 82.0, "source": "startup"}
    assert planning.daily_report_plans(nightly, replan, "2026-10-02") == [nightly, replan]
    # No re-plan: latest is the nightly plan itself — reported once.
    assert planning.daily_report_plans(nightly, nightly, "2026-10-02") == [nightly]
    # Stale nightly (HA down at 01:55): only today's startup plan.
    stale = {**nightly, "date": "2026-10-01"}
    assert planning.daily_report_plans(stale, replan, "2026-10-02") == [replan]
    assert planning.daily_report_plans({}, {}, "2026-10-02") == []


def _cost_day(date, imp=None, exp=None, gas=None):
    return {"date": date, "actual_import_cost_gbp": imp, "actual_export_income_gbp": exp, "actual_gas_cost_gbp": gas}


def test_latest_complete_cost_day_all_present(planning):
    rec = planning.latest_complete_cost_day([_cost_day("2026-10-05", 3.0, 1.0, 1.5)], "2026-10-01")
    assert rec == {
        "type": "daily_cost_complete", "date": "2026-10-05",
        "actual_import_cost_gbp": 3.0, "actual_export_income_gbp": 1.0, "actual_gas_cost_gbp": 1.5,
        "net_cost_gbp": 3.5,
    }


def test_latest_complete_cost_day_skips_lagging_gas(planning):
    days = [_cost_day("2026-10-05", 3.0, 1.0, None), _cost_day("2026-10-04", 2.0, 0.5, 1.0)]
    rec = planning.latest_complete_cost_day(days, "2026-10-01")
    assert rec["date"] == "2026-10-04" and rec["net_cost_gbp"] == 2.5


def test_latest_complete_cost_day_none_complete(planning):
    days = [_cost_day("2026-10-05", None, 1.0, None), _cost_day("2026-10-04", 2.0, None, 1.0)]
    assert planning.latest_complete_cost_day(days, "2026-10-01") is None
    assert planning.latest_complete_cost_day([], "2026-10-01") is None


def test_latest_complete_cost_day_picks_newest_in_window(planning):
    # Unordered input; zero is a real value, not missing; out-of-window days ignored.
    days = [
        _cost_day("2026-10-02", 1.0, 1.0, 1.0),
        _cost_day("2026-10-03", 0.0, 0.0, 0.0),
        _cost_day("2026-09-20", 9.0, 0.0, 9.0),
    ]
    assert planning.latest_complete_cost_day(days, "2026-10-01")["date"] == "2026-10-03"
    assert planning.latest_complete_cost_day(days[2:], "2026-10-01") is None


# --- Phase 19: daily full-charge-day re-check ---

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _move(planning, **overrides):
    kwargs = dict(
        tomorrow="Tuesday",
        chosen="Thursday",
        tomorrow_kwh=20.0,
        needed_kwh=16.0,
        tomorrow_score=80.0,
        chosen_score=40.0,
        already_moved=False,
        weekdays=_WEEKDAYS,
    )
    kwargs.update(overrides)
    return planning.full_charge_day_move(**kwargs)


def test_solar_kwh_to_fill(planning):
    assert planning.solar_kwh_to_fill(10.0, 0.75) == 16.0


def test_full_charge_move_sunny_tomorrow_better_weather(planning):
    assert _move(planning) == (True, "moved")


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"tomorrow": "Saturday"}, "not_weekday"),
        ({"tomorrow": "Thursday"}, "already_chosen"),
        ({"tomorrow": "Friday"}, "chosen_passed"),
        ({"already_moved": True}, "already_moved"),
        ({"tomorrow_kwh": None}, "no_forecast"),
        ({"tomorrow_kwh": 15.9}, "not_enough_solar"),
        ({"tomorrow_score": None}, "no_weather"),
        ({"chosen_score": None}, "no_weather"),
        ({"tomorrow_score": 40.0}, "chosen_looks_better"),
    ],
)
def test_full_charge_move_blocked(planning, overrides, reason):
    assert _move(planning, **overrides) == (False, reason)


def test_full_charge_move_exact_threshold_moves(planning):
    assert _move(planning, tomorrow_kwh=16.0) == (True, "moved")


# --------------------------------------------------------------------------- #
# Phase 20: recency-weighted, per-band forecast correction                     #
# --------------------------------------------------------------------------- #
def _corr_day(age, ratio, band="summer_like", today="2026-10-06"):
    from datetime import date, timedelta

    d = date.fromisoformat(today) - timedelta(days=age)
    return {"date": d.isoformat(), "solar_forecast_kwh": 10.0, "actual_solar_kwh": 10.0 * ratio, "forecast_band": band}


def test_weighted_correction_needs_min_days(planning):
    from datetime import date

    days = [_corr_day(a, 1.5) for a in range(1, 7)]
    assert planning.weighted_forecast_correction(days, date(2026, 10, 6)) == (1.0, "none")


def test_weighted_correction_favours_recent_days(planning):
    from datetime import date

    # 8 recent days at 0.8, 10 older days at 1.4: plain median would be 1.4.
    days = [_corr_day(a, 0.8) for a in range(1, 9)] + [_corr_day(a, 1.4) for a in range(15, 25)]
    factor, basis = planning.weighted_forecast_correction(days, date(2026, 10, 6))
    assert (factor, basis) == (0.8, "global")


def test_weighted_correction_uses_band_when_enough_days(planning):
    from datetime import date

    days = [_corr_day(a, 1.2, "winter_like") for a in range(1, 8)] + [_corr_day(a, 0.9) for a in range(1, 20)]
    assert planning.weighted_forecast_correction(days, date(2026, 10, 6), "winter_like") == (1.2, "band")
    # Too few days in the band → global fallback.
    assert planning.weighted_forecast_correction(days[3:], date(2026, 10, 6), "winter_like") == (0.9, "global")


@pytest.mark.parametrize("ratio,expected", [(5.0, 3.0), (0.1, 0.5)])
def test_weighted_correction_capped(planning, ratio, expected):
    from datetime import date

    days = [_corr_day(a, ratio) for a in range(1, 10)]
    assert planning.weighted_forecast_correction(days, date(2026, 10, 6)) == (expected, "global")


def test_weighted_correction_skips_tiny_forecasts_and_bad_dates(planning):
    from datetime import date

    days = [_corr_day(a, 1.1) for a in range(1, 8)]
    days.append({"date": "2026-10-05", "solar_forecast_kwh": 0.2, "actual_solar_kwh": 9.0})
    days.append({"date": None, "solar_forecast_kwh": 10.0, "actual_solar_kwh": 30.0})
    assert planning.weighted_forecast_correction(days, date(2026, 10, 6)) == (1.1, "global")


# Phase 12: learned household load. 2026-10-06 is a Tuesday.
def _load_day(age, kwh, today="2026-10-06", **extra):
    from datetime import date, timedelta

    d = date.fromisoformat(today) - timedelta(days=age)
    return {"date": d.isoformat(), "overnight_load_kwh": kwh, **extra}


def test_learned_load_needs_min_days(planning):
    from datetime import date

    days = [_load_day(a, 3.0) for a in range(1, 7)]
    assert planning.learned_load_kw(days, date(2026, 10, 6)) == (None, 6)


def test_learned_load_median_rate(planning):
    from datetime import date

    days = [_load_day(a, kwh) for a, kwh in enumerate([3.0, 3.6, 4.2, 4.8, 5.4, 6.0, 30.0], start=1)]
    # Median 4.8 kWh over 00:00-06:00 → 0.8 kW; the 30 kWh outlier doesn't move it.
    assert planning.learned_load_kw(days, date(2026, 10, 6)) == (0.8, 7)


def test_learned_load_excludes_away_full_missing_zero_and_old(planning):
    from datetime import date

    good = [_load_day(a, 3.0) for a in range(1, 8)]
    bad = [
        _load_day(8, 12.0, away=True),
        _load_day(9, 12.0, is_full_day=True),
        _load_day(10, None),
        _load_day(11, 0.0),
        _load_day(29, 12.0),  # outside the 28-day window
        _load_day(0, 12.0),  # today: not a complete past night
        {"date": None, "overnight_load_kwh": 12.0},
    ]
    assert planning.learned_load_kw(good + bad, date(2026, 10, 6)) == (0.5, 7)


def test_learned_load_weekend_split_with_fallback(planning):
    from datetime import date

    # 28 days back from Tue 06/10: 8 weekend days at 1.2 kW, 20 weekdays at 0.6 kW.
    days = [
        _load_day(a, 7.2 if date.fromordinal(date(2026, 10, 6).toordinal() - a).weekday() >= 5 else 3.6)
        for a in range(1, 29)
    ]
    today = date(2026, 10, 6)
    assert planning.learned_load_kw(days, today, weekend=True) == (1.2, 8)
    assert planning.learned_load_kw(days, today, weekend=False) == (0.6, 20)
    # Too few weekend days → pooled history; phase 111 lifts it to the last 3 nights' mean.
    assert planning.learned_load_kw(days[:14], today, weekend=True) == (1.0, 14)


@pytest.mark.parametrize(
    "learned,expected",
    [(None, (0.75, "config")), (0.9, (0.9, "learned")), (0.2, (0.375, "learned")), (3.0, (1.5, "learned"))],
)
def test_resolve_load_kw_clamps_to_config_band(planning, learned, expected):
    assert planning.resolve_load_kw(0.75, learned) == expected


# ---- Phase 22: charge watchdog --------------------------------------------- #


def _progress(planning, **overrides):
    # 3 kW into 10 kWh for 20 min → ~10% expected rise; 2.5% needed.
    kwargs = dict(start_soc=40.0, now_soc=40.0, minutes=20, expected_rate_kw=3.0, capacity_kwh=10.0,
                  grid_import_w=200.0, target_soc=80)
    kwargs.update(overrides)
    return planning.charge_progress_ok(**kwargs)


def test_charge_progress_rising_soc_is_ok(planning):
    assert _progress(planning, now_soc=45.0) == "ok"


def test_charge_progress_flat_soc_with_import_is_ok(planning):
    # SOC can lag a cloud poll; grid draw at charge power proves it's running.
    assert _progress(planning, grid_import_w=3200.0) == "ok"


def test_charge_progress_flat_soc_no_import_is_stalled(planning):
    assert _progress(planning) == "stalled"
    assert _progress(planning, now_soc=41.0) == "stalled"  # 1% is SOC noise, not progress


@pytest.mark.parametrize("missing", ["start_soc", "now_soc", "grid_import_w", "expected_rate_kw"])
def test_charge_progress_missing_inputs_are_unknown(planning, missing):
    assert _progress(planning, **{missing: None}) == "unknown"


def test_charge_progress_no_elapsed_time_is_unknown(planning):
    assert _progress(planning, minutes=0) == "unknown"


def test_charge_progress_at_target_is_ok(planning):
    assert _progress(planning, start_soc=79.0, now_soc=80.0) == "ok"


def test_charge_progress_small_gain_floor(planning):
    # 0.5 kW for 5 min → tiny expected rise; the 1% floor still applies.
    assert _progress(planning, minutes=5, expected_rate_kw=0.5, now_soc=40.5, grid_import_w=0.0) == "stalled"
    assert _progress(planning, minutes=5, expected_rate_kw=0.5, now_soc=41.0, grid_import_w=0.0) == "ok"


@pytest.mark.parametrize("hhmm,minutes", [("02:00", 120), ("05:00", 300), ("bad", None), (None, None)])
def test_hhmm_to_minutes(planning, hhmm, minutes):
    assert planning.hhmm_to_minutes(hhmm) == minutes


def test_evening_reserve_soc(planning):
    # 20% floor + 0.75 kW × 7 h / 10 kWh = 72.5 → 73.
    assert planning.evening_reserve_soc(0.75, 10.0) == 73
    assert planning.evening_reserve_soc(0.0, 10.0) == 20
    assert planning.evening_reserve_soc(2.0, 10.0) == 100  # capped
    assert planning.evening_reserve_soc(0.5, 10.0, hours=4.0, floor_soc=10) == 30


def test_trim_target_floored_at_reserve(planning):
    assert planning.trim_target_soc(90, 73) == 82  # reserve below default
    assert planning.trim_target_soc(90, 84) == 84  # reserve raises the floor
    assert planning.trim_target_soc(86, 88) is None  # nothing left to trim
    assert planning.trim_target_soc(100, 100) is None
    assert planning.trim_target_soc(90, None) == 82


def test_trim_never_at_night_or_below_tonights_charge(planning):
    # Overnight charge crossing 85% (03:00) must not be trimmed — the 12/10 stall at ~83%.
    assert planning.trim_target_soc(86, 73, minutes=3 * 60, plan_target_soc=95) is None
    assert planning.trim_target_soc(95, 73, minutes=4 * 60 + 59) is None
    assert planning.trim_target_soc(95, 73, minutes=19 * 60) is None
    assert planning.trim_target_soc(95, 73, minutes=23 * 60) is None
    # Daytime: never below what the plan bought; solar excess above it is still trimmed.
    assert planning.trim_target_soc(95, 73, minutes=5 * 60, plan_target_soc=95) is None
    assert planning.trim_target_soc(99, 73, minutes=11 * 60, plan_target_soc=90) == 90
    assert planning.trim_target_soc(95, 73, minutes=11 * 60, plan_target_soc=70) == 82
    # Peak window sells at the peak rate, so tonight's target no longer floors it.
    assert planning.trim_target_soc(95, 73, minutes=17 * 60, plan_target_soc=100) == 82


def test_restore_keeps_an_active_export_pause(planning):
    assert planning.restore_flux2_slot(True, 18 * 60)["targetSoc"] == 100
    assert planning.restore_flux2_slot(True, 19 * 60)["targetSoc"] == 85
    assert planning.restore_flux2_slot(False, 17 * 60)["targetSoc"] == 85
    # Returns a copy: callers can't mutate the shared default.
    planning.restore_flux2_slot(False, 0)["targetSoc"] = 1
    assert planning.DEFAULT_FLUX2_SLOT["targetSoc"] == 85


def test_battery_limits_bottom_20_low_25(planning):
    assert planning.BATTERY_BOTTOM_SOC == 20 and planning.BATTERY_LOW_SOC == 25
    assert planning.KPI_RESERVE_SOC == 20
    assert planning.PEAK_COVER_FLOOR_SOC == 25


@pytest.mark.parametrize(
    "now_minutes, attempts, due",
    [
        (1 * 60 + 55, 0, True),  # 01:55 failure → retry at 02:05
        (2 * 60 + 25, 3, True),  # 02:25 → 02:35, still inside the window
        (2 * 60 + 35, 4, False),  # 02:45 retry would be past 02:40
        (2 * 60 + 5, 5, False),  # attempt cap
        (14 * 60, 0, False),  # daytime startup push failure: no overnight retry
        (1 * 60, 0, False),  # 01:10 is before the window
    ],
)
def test_plan_retry_due(planning, now_minutes, attempts, due):
    assert planning.plan_retry_due(now_minutes, attempts) is due


_PUSHED = [
    {"startTime": "02:00", "endTime": "05:00", "targetSoc": 85, "direction": 1},
    {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85, "direction": 0},
]


def test_flux_drift_equal_and_hhmmss(planning):
    actual = [
        {"startTime": "02:00:00", "endTime": "05:00:00", "targetSoc": "85"},
        {"startTime": "16:00", "endTime": "16:15", "targetSoc": 85.0},
    ]
    assert planning.flux_drift(_PUSHED, actual) == []


def test_flux_drift_reports_differing_slot(planning):
    actual = [dict(_PUSHED[0]), {**_PUSHED[1], "targetSoc": 100}]
    assert planning.flux_drift(_PUSHED, actual) == [
        {"slot": 2, "field": "targetSoc", "expected": 85, "actual": 100}
    ]


def test_flux_drift_missing_fields_no_false_alarm(planning):
    actual = [{"startTime": "02:00"}, {"endTime": None, "targetSoc": "n/a"}]
    assert planning.flux_drift(_PUSHED, actual) == []
    assert planning.flux_drift(_PUSHED, []) == []
    assert planning.flux_drift(None, _PUSHED) == []


def test_expected_grid_current(planning):
    assert planning.expected_grid_current_a({"phase": "applied", "current_a": 40}, "2026-10-14") == 40
    restored = {"phase": "restored", "date": "2026-10-14", "restore_current_a": 90}
    assert planning.expected_grid_current_a(restored, "2026-10-14") == 90
    assert planning.expected_grid_current_a(restored, "2026-10-15") is None
    assert planning.expected_grid_current_a({}, "2026-10-14") is None


def test_grid_current_drift(planning):
    assert planning.grid_current_drift(40.0, "40") == []
    assert planning.grid_current_drift(40.0, "40.5") == []
    assert planning.grid_current_drift(None, "90") == []
    assert planning.grid_current_drift(40.0, None) == []
    assert planning.grid_current_drift(40.0, "90") == [
        {"slot": None, "field": "grid_charge_current_a", "expected": 40.0, "actual": 90.0}
    ]


def test_tariff_price_changes(planning):
    configured = {"offpeak": 14.0, "day": 23.28, "peak": 32.6, "export_peak": 28.5}
    assert planning.tariff_price_changes(configured, dict(configured)) == []
    assert planning.tariff_price_changes(configured, {"day": 23.284}) == []  # rounding
    assert planning.tariff_price_changes(configured, {"day": 24.10, "peak": None}) == [("day", 23.28, 24.1)]
    # Missing Octopus band or missing configured price: ignored, never "0p".
    assert planning.tariff_price_changes({"day": None}, {"day": 24.1, "offpeak": 0.0}) == []


def test_low_solar_run(planning):
    t = planning.LOW_SOLAR_THRESHOLD_KWH
    assert planning.low_solar_run([t - 1, t - 2]) == 2
    assert planning.low_solar_run([t - 1, t + 5]) == 1
    assert planning.low_solar_run([t + 1, t - 1]) == 0
    assert planning.low_solar_run([t - 1, None]) == 1  # missing forecast ends the run
    assert planning.low_solar_run([]) == 0


def test_multi_day_peak_floor_soc(planning):
    # 14 h × 0.5 kW = 7 kWh load, 2 kWh solar, 3 kWh refill → 2 kWh short of 10 kWh = +20%.
    assert planning.multi_day_peak_floor_soc(30, 10.0, 7.0, 2.0, 3.0) == 50
    assert planning.multi_day_peak_floor_soc(30, 10.0, 4.0, 2.0, 3.0) == 30  # refill covers it
    assert planning.multi_day_peak_floor_soc(30, 10.0, 50.0, 0.0, 0.0) == 100  # clamped
    assert planning.multi_day_peak_floor_soc(30, 10.0, 7.0, None, 3.0) is None
    assert planning.multi_day_peak_floor_soc(30, None, 7.0, 2.0, 3.0) is None


def test_multi_day_shadow(planning):
    assert planning.multi_day_shadow(1, target_soc=60) is None  # single low day: unchanged
    assert planning.multi_day_shadow(0, peak_target_soc=40, floor_soc=60) is None
    plan = planning.multi_day_shadow(2, target_soc=80)
    assert plan["would_target_soc"] == 100 and plan["changes_target"] and plan["trims_off"]
    peak = planning.multi_day_shadow(2, peak_target_soc=40, floor_soc=60)
    assert peak["would_peak_target_soc"] == 60 and peak["changes_peak"]
    same = planning.multi_day_shadow(3, peak_target_soc=40, floor_soc=None)
    assert same["would_peak_target_soc"] == 40 and not same["changes_peak"]


@pytest.mark.parametrize(
    "now_minutes, due",
    [
        (115, True),   # 01:55 skip → retry at 02:00
        (145, True),   # 02:25 → last retry at 02:30
        (146, False),  # 02:31 would be past 02:30
        (12 * 60, False),  # a daytime re-plan never loops
        (80, False),   # 01:20 + 5 is before 01:30
    ],
)
def test_soc_retry_due(planning, now_minutes, due):
    assert planning.soc_retry_due(now_minutes) is due


def test_recent_soc(planning):
    from datetime import timedelta, timezone

    now = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
    assert planning.recent_soc(41.0, (30.0, now - timedelta(minutes=5)), now) == (41.0, False)
    assert planning.recent_soc(None, (30.0, now - timedelta(minutes=29)), now) == (30.0, True)
    assert planning.recent_soc(None, (30.0, now - timedelta(minutes=31)), now) == (None, False)
    assert planning.recent_soc(None, (30.0, now + timedelta(minutes=1)), now) == (None, False)
    assert planning.recent_soc(None, None, now) == (None, False)


# ---- Phase 41: shadow go-live scorecard ---------------------------------

def _days(n, end="2026-11-01"):
    from datetime import date, timedelta
    e = date.fromisoformat(end)
    return [(e - timedelta(days=i)).isoformat() for i in range(n)][::-1]


def test_scorecard_no_records_and_silent_failure(planning):
    card = planning.shadow_scorecard([], "2026-11-01")
    assert {v["verdict"] for v in card.values()} == {"no_records"}
    old = [{"type": "peak_export", "date": "2026-10-05", "decision": "export", "gain_gbp": 0.5}]
    assert planning.shadow_scorecard(old, "2026-11-01")["peak_export"]["verdict"] == "no_records"


def test_scorecard_peak_export_ready_and_not_yet(planning):
    recs = [{"type": "peak_export", "date": d, "decision": "export", "gain_gbp": 0.1} for d in _days(8)]
    s = planning.shadow_scorecard(recs, "2026-11-01")["peak_export"]
    assert s["verdict"] == "ready" and s["changed"] == 8 and s["delta_gbp"] == 0.8
    quiet = [{"type": "peak_export", "date": d, "decision": "no_surplus"} for d in _days(7)]
    s = planning.shadow_scorecard(quiet, "2026-11-01")["peak_export"]
    assert s["verdict"] == "not_yet" and s["delta_gbp"] is None


def test_scorecard_soc_cap_harmful_and_missing_stays_none(planning):
    recs = [{"type": "day_kpis", "date": d, "soc_cap_shadow": {"extra_cost_gbp": 0.14}} for d in _days(5)]
    s = planning.shadow_scorecard(recs, "2026-11-01")["soc_cap_85"]
    assert s["verdict"] == "harmful" and s["delta_gbp"] == -0.7
    none = [{"type": "day_kpis", "date": "2026-10-30", "soc_cap_shadow": {"extra_cost_gbp": None}}]
    s = planning.shadow_scorecard(none, "2026-11-01")["soc_cap_85"]
    assert s["delta_gbp"] is None and s["verdict"] == "not_yet"


def test_scorecard_weighted_correction_compares_error(planning):
    recs = []
    for d in _days(8):
        recs.append({"type": "import_plan", "date": d, "raw_forecast_kwh": 10, "forecast_correction_factor": 1.0,
                     "forecast_correction_weighted": 0.8})
        recs.append({"type": "day_actuals", "date": d, "actual_solar_kwh": 8})
    s = planning.shadow_scorecard(recs, "2026-11-01")["weighted_correction"]
    assert s["verdict"] == "ready" and s["error_ratio"] == 0.0
    for r in recs:
        if r["type"] == "import_plan":
            r["forecast_correction_weighted"] = 1.5
    assert planning.shadow_scorecard(recs, "2026-11-01")["weighted_correction"]["verdict"] == "harmful"


def test_scorecard_multi_day_capacity_and_summary(planning):
    recs = [{"type": "multi_day_plan", "date": "2026-10-30", "changes_target": True},
            {"type": "capacity_estimate", "date": "2026-10-05", "estimate_kwh": 9.4, "pct_of_configured": 94.0}]
    card = planning.shadow_scorecard(recs, "2026-11-01")
    assert card["multi_day"]["changed"] == 1 and card["multi_day"]["verdict"] == "not_yet"
    assert card["capacity"]["verdict"] == "ready" and card["capacity"]["estimate_kwh"] == 9.4
    assert "capacity: ready" in planning.scorecard_summary(card)


# Phase 54: charge outcome
def test_charge_outcome_causes_and_alert(planning):
    f = planning.charge_outcome
    assert f(None, 90) is None and f(100, None) is None       # rule 11
    assert f(100, 100)["likely_cause"] is None and not f(100, 100)["alert"]
    o = f(100, 95)
    assert o["likely_cause"] == "taper" and not o["alert"]     # 5 short is not > 5
    assert f(100, 90)["likely_cause"] == "stopped_early" and f(100, 90)["alert"]
    assert f(80, 70, battery_temp_c=5)["likely_cause"] == "cold_derate"
    assert f(80, 85)["shortfall_pct"] == -5.0


def test_achievable_max_soc_median_of_high_targets(planning):
    outs = [{"target_soc": 100, "reached_soc": v} for v in (94, 98, 96)]
    outs.append({"target_soc": 60, "reached_soc": 20})
    outs.append({"target_soc": 100, "reached_soc": None})
    assert planning.achievable_max_soc(outs) == 96
    assert planning.achievable_max_soc([]) is None


# Phase 57: monthly report catch-up
def test_monthly_report_due(planning):
    from datetime import date
    f = planning.monthly_report_due
    # Nov 2026: first Sunday is the 1st
    assert f(None, date(2026, 11, 1)) is True
    assert f("2026-10", date(2026, 11, 1)) is False       # already sent
    assert f("2026-09", date(2026, 11, 20)) is True       # missed Sunday catches up
    # Oct 2026: first Sunday is the 4th; not due on the 2nd
    assert f("2026-08", date(2026, 10, 2)) is False
    assert f("2026-08", date(2026, 10, 4)) is True
    assert f("2026-09", date(2026, 10, 9)) is False       # Sept report sent


# Phase 61: measured charge rate
def test_measured_charge_kw(planning):
    f = planning.measured_charge_kw
    # 3 h window: 9.9 kWh import, 1.8 kWh load -> 8.1 * 0.92 / 3 = 2.48
    assert f(9.9, 1.8, "05:00", 20, 100) is None            # full window skipped
    assert f(9.9, 1.8, "04:45", 20, 100) == round((9.9 - 1.8 * 2.75 / 3) * 0.92 / 2.75, 2)
    assert f(None, 1.8, "04:00", 20, 100) is None            # rule 11
    assert f(5, 1, "04:00", 20, 100, soc_cached=True) is None
    assert f(5, 1, "02:15", 20, 100) is None                 # window under 30 min
    assert f(5, 1, "04:00", 98, 100) is None                 # barely charged
    assert f(1, 3, "04:00", 20, 100) is None                 # load exceeds import
    assert f(30, 0, "03:00", 20, 100) is None                # implausible kW


def test_learned_measured_rate(planning):
    f = planning.learned_measured_rate
    assert f([(2.5, 20), (2.4, 20)], 20) is None              # < 3 samples
    # three samples in tonight's band: used directly, no further derate
    assert f([(2.0, 12), (2.2, 13), (2.1, 11)], 12) == (2.15, True)
    # no samples at tonight's temperature: normalised to warm, caller derates
    kw, at_temp = f([(2.55, 12), (2.55, 12), (2.55, 12)], 22)
    assert at_temp is False and kw == 3.0                     # 2.55 / 0.85


def test_resolve_used_charge_rate_no_derate(planning):
    assert planning.resolve_used_charge_rate(3.0, 2.0, 8, derate=False) == (2.0, 0.70)


def test_blend_lifts_marginal_forecast_toward_full(planning):
    # 7.19 kWh was 45% before; just above the 7 kWh line it must now sit near 100%.
    near = _select(planning, low_solar_forecast_kwh=7.19, band="shoulder", hours_to_solar=4.9, avg_consumption_kw=0.417)
    assert near.reason == "solar_bridge_blend" and near.target_soc >= 90
    mid = _select(planning, low_solar_forecast_kwh=8.5, band="shoulder", hours_to_solar=4.9, avg_consumption_kw=0.417)
    assert near.target_soc > mid.target_soc > 50
    # At and above the blend end the plain bridge is used.
    far = _select(planning, low_solar_forecast_kwh=10.0, band="summer_like", hours_to_solar=4.9, avg_consumption_kw=0.417)
    assert far.reason == "solar_bridge"
    # Below the line the existing override still wins.
    assert _select(planning, low_solar_forecast_kwh=6.99, band="shoulder").target_soc in (95, 100)


def test_simple_bridge_carries_load_margin(planning):
    d = _select(planning, hours_to_solar=4.0, avg_consumption_kw=0.5, battery_capacity_kwh=10.0)
    assert d.target_soc == planning.bridge_soc(4.0 * 0.5 * planning.BRIDGE_LOAD_MARGIN, 10.0) > planning.bridge_soc(2.0, 10.0)


def test_blend_never_lowers_or_touches_full(planning):
    d = planning.TargetDecision(100, "solar_bridge")
    assert planning.blend_toward_full(d, 7.5).reason == "solar_bridge"
    d = planning.TargetDecision(80, "solar_bridge")
    assert planning.blend_toward_full(d, 12.0).target_soc == 80


def test_pack_lines_keeps_whole_lines(planning):
    pack_lines = planning.pack_lines
    lines = ["a" * 2600, "b" * 1700, "c" * 860, "d" * 700, "e" * 5000]
    packs = pack_lines(lines)
    assert [l for p in packs for l in p] == lines
    assert all(len("\n".join(p)) <= 3500 or len(p) == 1 for p in packs)
    assert packs[-1] == ["e" * 5000]
    assert pack_lines(["x"]) == [["x"]] and pack_lines([]) == [[""]]


def test_learned_load_follows_recent_cold_snap(planning):
    from datetime import date, timedelta
    today = date(2026, 11, 12)
    days = [{"date": (today - timedelta(days=i)).isoformat(), "overnight_load_kwh": 2.5} for i in range(3, 20)]
    days += [{"date": (today - timedelta(days=1)).isoformat(), "overnight_load_kwh": 4.3},
             {"date": (today - timedelta(days=2)).isoformat(), "overnight_load_kwh": 3.1}]
    kw, n = planning.learned_load_kw(days, today)
    assert n == 19 and kw == round((4.3 + 3.1 + 2.5) / 3 / 6, 3)  # recent mean beats 0.417 median
    # A quiet recent spell never lowers the median.
    low = [dict(d, overnight_load_kwh=1.0) if i >= 17 else d for i, d in enumerate(days)]
    assert planning.learned_load_kw(low, today)[0] == round(2.5 / 6, 3)
