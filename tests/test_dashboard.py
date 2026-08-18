"""Structural tests for the generated Lovelace dashboard.

The dashboard is a hand-built dict serialised with json.dumps; a malformed
card (e.g. a tuple key, a non-serialisable value) would only surface at install
time in Home Assistant. These tests catch that in CI.
"""

from __future__ import annotations

import json

import pytest

import conftest

_build_dashboard = conftest._dashboard._build_dashboard

_CONFIG = {
    "inverter_serial": "2411132500",
    "plant_id": "111111",
    "solar_forecast_sensor": "sensor.energy_production_today",
    "weather_entity": "weather.forecast_home",
    "avg_consumption_kw": 0.75,
}


@pytest.fixture(scope="module")
def dashboard():
    return _build_dashboard(_CONFIG)


def test_dashboard_is_json_serialisable(dashboard):
    # Lovelace YAML-mode loads this via json.dumps; must not raise.
    assert json.dumps(dashboard)


def test_dashboard_has_expected_top_level_shape(dashboard):
    assert dashboard["title"]
    assert dashboard["views"] and dashboard["views"][0]["type"] == "sections"
    assert len(dashboard["views"][0]["sections"]) >= 6


def test_dashboard_ids_are_sanitised_into_entities(dashboard):
    # The serial flows into entity IDs via the s() helper; a clean serial passes through.
    blob = json.dumps(dashboard)
    assert "sensor.solarsynkv3_2411132500_battery_soc" in blob


def test_dashboard_surfaces_new_plan_fields(dashboard):
    blob = json.dumps(dashboard)
    for field in (
        "low_solar_forecast_kwh",   # b33
        "charge_rate_from_cache",   # b35
        "synthetic_ramp",           # b36
        "used_charge_rate_kw",
    ):
        assert field in blob, f"{field} missing from dashboard"


def test_dashboard_has_soc_gauge(dashboard):
    blob = json.dumps(dashboard)
    assert '"type": "gauge"' in blob
    assert "Battery SOC now" in blob


def test_dashboard_has_consumption_section(dashboard):
    # v1.0.10b3: the overnight/day/peak-window load+grid fields added in
    # b1/b2 are read from sensor.consumption's attributes.
    blob = json.dumps(dashboard)
    assert "Consumption" in blob
    for field in (
        "overnight_load_kwh",
        "day_load_kwh",
        "day_grid_import_kwh",
        "day_grid_export_kwh",
        "peak_load_kwh",
        "peak_grid_import_kwh",
        "peak_grid_export_kwh",
    ):
        assert f'"attribute": "{field}"' in blob, f"{field} not wired into the dashboard"
        assert '"entity": "sensor.consumption"' in blob


def test_malicious_id_cannot_inject_template(dashboard):
    # A serial containing Jinja/quote chars is sanitised before interpolation.
    hostile = dict(_CONFIG, inverter_serial="1{{states('x')}}", plant_id="9'\"}{9")
    blob = json.dumps(_build_dashboard(hostile))
    assert "{{states" not in blob
    assert "sensor.solarsynkv3_1statesx_battery_soc" in blob
