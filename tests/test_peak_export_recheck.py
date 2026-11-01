"""Tests for phase 66: the 17:00/18:00 peak export re-check from the observed load."""

from __future__ import annotations

EXPORT, OFFPEAK = 15.0, 7.5


def _rc(planning, **overrides):
    args = dict(
        soc=85.0, reserve_soc=40, battery_capacity_kwh=10.0, elapsed_minutes=60, minutes_left=120,
        observed_peak_kwh=1.0, prior_target_soc=80, prior_pushed=False,
        export_pence=EXPORT, offpeak_pence=OFFPEAK,
    )
    args.update(overrides)
    return planning.peak_export_recheck(**args)


def test_light_load_lowers_the_target_and_sells_more(planning):
    # 1.0 kWh in the first hour -> 2.0 kWh for the last two; x1.1 / 0.92 / 10 kWh = 24 SOC points.
    result = _rc(planning)
    assert result["remaining_load_kwh"] == 2.0
    assert result["target_soc"] == 40 + 24 + planning.PEAK_EXPORT_MARGIN_SOC
    assert result["decision"] == "export" and result["action"] == "lower"
    assert result["move_kwh"] == round((80 - result["target_soc"]) / 10.0, 2)


def test_small_move_is_not_pushed(planning):
    # Target moves < 0.5 kWh versus the 16:00 plan.
    base = _rc(planning)
    result = _rc(planning, prior_target_soc=base["target_soc"] + 2)
    assert result["action"] is None


def test_target_never_below_reserve_plus_margin(planning):
    result = _rc(planning, observed_peak_kwh=0.0)
    assert result["target_soc"] == 40 + planning.PEAK_EXPORT_MARGIN_SOC


def test_heavy_load_raises_target_only_if_the_plan_was_pushed(planning):
    heavy = _rc(planning, observed_peak_kwh=2.5, prior_target_soc=60, soc=65.0)
    assert heavy["action"] is None  # shadow / unpushed: nothing to undo
    pushed = _rc(planning, observed_peak_kwh=2.5, prior_target_soc=60, soc=65.0, prior_pushed=True)
    assert pushed["action"] == "raise" and pushed["target_soc"] > 60


def test_missing_inputs_are_skipped_not_zero(planning):
    assert _rc(planning, soc=None)["decision"] == "skipped_no_soc"
    assert _rc(planning, observed_peak_kwh=None)["decision"] == "skipped_no_observed_load"
    assert _rc(planning, elapsed_minutes=0)["decision"] == "skipped_no_observed_load"
    assert _rc(planning, export_pence=None)["action"] is None


def test_starts_export_when_the_16_00_plan_had_no_target_move_info(planning):
    result = _rc(planning, prior_target_soc=None)
    assert result["action"] == "lower" and result["move_kwh"] is None
