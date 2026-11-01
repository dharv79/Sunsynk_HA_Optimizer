"""Tests for phase 58: plain-English plan explanation (one per reason code)."""

from __future__ import annotations


def _plan(**kw):
    base = {
        "target_soc": 100, "target_soc_reason": "low_solar_override", "solar_forecast_kwh": 4.4,
        "raw_forecast_kwh": 4.0, "forecast_correction_factor": 1.1, "low_solar_forecast_kwh": 4.0,
        "overnight_drain_adjustment": 0, "soc_adjustment": 0, "forecast_band": "winter_like",
        "logic_branch": "low_solar_full_window",
    }
    base.update(kw)
    return base


def _text(planning, plan, shadow=None):
    return " ".join(planning.explain_plan(plan, shadow))


def test_low_solar_override(planning):
    text = _text(planning, _plan(target_soc=95))
    assert "Target 95%" in text and "4.0 kWh" in text and "7 kWh line" in text
    assert "raw 4.0 kWh x 1.1" in text


def test_low_solar_winter_like(planning):
    assert "low-solar winter night" in _text(planning, _plan(target_soc_reason="low_solar_override_winter_like"))


def test_weekly_full_charge_day(planning):
    assert "weekly full-charge day" in _text(planning, _plan(target_soc_reason="weekly_full_charge_day"))


def test_solar_bridge_variants(planning):
    hourly = _text(planning, _plan(target_soc=60, target_soc_reason="solar_bridge_hourly", bridge_hour=9))
    assert "around 09:00" in hourly
    assert "slow-ramp" in _text(planning, _plan(target_soc=60, target_soc_reason="solar_bridge_ramp"))
    assert "until solar takes over" in _text(planning, _plan(target_soc=60, target_soc_reason="solar_bridge"))


def test_band_fallback_without_sun(planning):
    text = _text(planning, _plan(target_soc=95, target_soc_reason="winter_like"))
    assert "no solar start time" in text and "winter-like" in text


def test_adjustments_cap_and_full_window(planning):
    text = _text(planning, _plan(
        overnight_drain_adjustment=20, soc_adjustment=-5, import_feedback_adjustment=3,
        import_feedback_live=False, free_event_capped=True, logic_branch="full_window"))
    assert "+20% for overnight drain" in text and "-5% evening nudge" in text
    assert "+3% (logged only)" in text and "free-electricity event" in text and "05:00" in text


def test_shadow_counterfactual(planning):
    assert "£1.25 extra" in _text(planning, _plan(), {"cap_soc": 85, "extra_cost_gbp": 1.25})
    assert "could not be costed" in _text(planning, _plan(), {"cap_soc": 85, "extra_cost_gbp": None})


def test_missing_data_is_left_out_not_zero(planning):
    assert planning.explain_plan(None) == []
    assert planning.explain_plan({}) == []
    text = _text(planning, {"target_soc": 80, "target_soc_reason": "solar_bridge"})
    assert "Forecast" not in text and "drain" not in text
