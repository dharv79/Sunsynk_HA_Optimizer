"""Phase 78: household electrification and sizing advisor (report only)."""

from __future__ import annotations

import pytest

from conftest import _planning as planning


def _days(n=20, gas=2.0, clipped=1.5, start=1):
    return [
        {"date": f"2026-01-{i:02d}", "actual_gas_cost_gbp": gas, "clipped_solar_kwh": clipped, "away": False}
        for i in range(start, start + n)
    ]


def test_cop_curve_is_clamped_and_defaults():
    assert planning.heat_pump_cop(0) == planning.HEAT_PUMP_COP_AT_0C
    assert planning.heat_pump_cop(-30) == planning.HEAT_PUMP_COP_RANGE[0]
    assert planning.heat_pump_cop(40) == planning.HEAT_PUMP_COP_RANGE[1]
    assert planning.heat_pump_cop(None) == planning.HEAT_PUMP_COP_DEFAULT
    assert planning.heat_pump_cop(10) > planning.heat_pump_cop(0)


def test_mean_band_price_cosy():
    rates = planning.merge_tariff_rates(None)["cosy"]
    mean = planning.mean_band_price(planning.bands_from_rates(planning.TARIFF_IMPORT_BANDS["cosy"], rates))
    # 6 h cheap, 3 h peak, 15 h day.
    assert mean == pytest.approx((6 * 13.5 + 3 * 40.5 + 15 * 27.0) / 24, abs=0.01)
    assert planning.mean_band_price([(0, 60, 10.0)]) is None  # gaps → None


def test_heat_pump_saving_maths():
    days = _days(gas=2.0)
    temps = {d["date"]: 0.0 for d in days}
    hp = planning.heat_pump_scenario(days, 6.0, 12.0, gas_standing_pence=30.0, install_price_gbp=8000, temps=temps)
    gas_kwh = (200 - 30) / 6.0
    elec = gas_kwh * planning.BOILER_EFFICIENCY / planning.HEAT_PUMP_COP_AT_0C
    per_day = (200 - elec * 12.0) / 100
    assert per_day > 0
    assert hp["days"] == 20
    assert hp["saving_gbp"] == pytest.approx(per_day * 20, abs=0.01)
    assert hp["annual_gbp"] == pytest.approx(per_day * 365, abs=0.01)
    assert hp["payback_years"] == pytest.approx(8000 / (per_day * 365), abs=0.1)
    assert hp["mean_cop"] == planning.HEAT_PUMP_COP_AT_0C
    assert hp["confidence"] == "low" and hp["assumptions"]


def test_heat_pump_skips_away_and_missing_and_needs_inputs():
    days = _days(n=14)
    days[0]["away"] = True
    days[1]["actual_gas_cost_gbp"] = None
    assert planning.heat_pump_scenario(days, 6.0, 25.0) is None  # 12 usable < 14
    assert planning.heat_pump_scenario(_days(), None, 25.0) is None
    assert planning.heat_pump_scenario(_days(), 6.0, None) is None
    # A negative saving gives no payback.
    costly = planning.heat_pump_scenario(_days(gas=0.3), 6.0, 200.0, install_price_gbp=8000)
    assert costly["annual_gbp"] < 0 and costly["payback_years"] is None


def test_export_limit_scenario():
    out = planning.export_limit_scenario(_days(clipped=1.5), 15.0, price_gbp=300)
    assert out["clipped_kwh"] == 30.0
    assert out["saving_gbp"] == 4.5
    assert out["payback_years"] == pytest.approx(300 / (0.225 * 365), abs=0.1)
    assert planning.export_limit_scenario(_days(), None) is None
    assert planning.export_limit_scenario(_days(n=5), 15.0) is None


def test_summary_lines():
    report = {
        "heat_pump": planning.heat_pump_scenario(_days(), 6.0, 25.0),
        "export_limit": planning.export_limit_scenario(_days(), 15.0),
    }
    lines = planning.electrification_summary(report)
    assert len(lines) == 2 and "Heat pump" in lines[0] and "low confidence" in lines[0]
    assert planning.electrification_summary({}) == []
    worse = planning.electrification_summary({"heat_pump": planning.heat_pump_scenario(_days(gas=0.3), 6.0, 200.0)})
    assert "more" in worse[0] and "£-" not in worse[0]


def test_upgrade_options_flat_and_sorted():
    report = {
        "days": 200,
        "scenarios": [{"name": "+5 kWh battery", "annual_gbp": 90.0, "saving_gbp": 49.3, "payback_years": 12.0},
                      {"name": "+10 kWh battery", "annual_gbp": 130.0, "saving_gbp": 71.2, "payback_years": None}],
        "heat_pump": planning.heat_pump_scenario(_days(gas=0.3), 6.0, 200.0),
        "export_limit": None,
    }
    rows = planning.upgrade_options(report)
    assert [r["name"] for r in rows] == ["+10 kWh battery", "+5 kWh battery", "Heat pump (Cosy) vs gas"]
    assert rows[-1]["annual_gbp"] < 0 and rows[-1]["confidence"] == "low"
    assert rows[0]["days"] == 200
    assert planning.upgrade_options(None) == [] and planning.upgrade_options({"days": 3, "scenarios": []}) == []
