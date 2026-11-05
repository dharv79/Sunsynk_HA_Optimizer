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
    views = dashboard["views"]
    assert len(views) >= 6
    assert all(v["type"] == "sections" and v["sections"] for v in views)
    paths = [v["path"] for v in views]
    assert len(paths) == len(set(paths))
    # Existing sidebar links and bookmarks open the first view.
    assert paths[0] == "sunsynk-optimizer"


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
        assert f"'sensor.consumption', '{field}'" in blob, f"{field} not wired into the dashboard"


def test_dashboard_has_cost_cards(dashboard):
    # v1.0.11b1: Octopus daily/year-to-date cost fields, read from the same
    # sensor.consumption entity as the other Consumption-section cards.
    blob = json.dumps(dashboard)
    for field in (
        "year_to_date_net_cost_gbp",
        "year_to_date_days_counted",
        "daily_cost_date",
        "actual_import_cost_gbp",
        "actual_export_income_gbp",
        "actual_gas_cost_gbp",
        "net_cost_gbp",
    ):
        assert f"'sensor.consumption', '{field}'" in blob, f"{field} not wired into the dashboard"


def test_malicious_id_cannot_inject_template(dashboard):
    # A serial containing Jinja/quote chars is sanitised before interpolation.
    hostile = dict(
        _CONFIG, inverter_serial="1{{states('x')}}", plant_id="9'\"}{9",
        solar_forecast_sensor="sensor.x') }}{{ 7", weather_entity="{{ bad }}",
    )
    blob = json.dumps(_build_dashboard(hostile))
    assert "{{states" not in blob and "{{ 7" not in blob and "{{ bad" not in blob
    assert "sensor.solarsynkv3_1statesx_battery_soc" in blob
    assert "sensor.x7" in blob and "weather.forecast_home" in blob


def test_dashboard_surfaces_bill_check_and_capacity(dashboard):
    blob = json.dumps(dashboard)
    for entity in ("number.billed_last_month", "sensor.battery_capacity_estimate", "sensor.cost_of_1_kwh_now"):
        assert entity in blob, f"{entity} missing from dashboard"


def test_free_event_card_reads_only_always_present_attributes(dashboard):
    # Spook flags a dashboard attribute the entity doesn't have (19/10: expected_export_kwh).
    planning = conftest._planning
    used = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("entity") == "sensor.free_event" and node.get("attribute"):
                used.add(node["attribute"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(dashboard)
    assert used and used <= set(planning.FREE_EVENT_ATTRIBUTE_KEYS)


def _cards(node):
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for value in node.values():
            yield from _cards(value)
    elif isinstance(node, list):
        for value in node:
            yield from _cards(value)


def test_dashboard_surfaces_optimizer_entities(dashboard):
    blob = json.dumps(dashboard)
    for entity in (
        "sensor.mpc_plan", "sensor.dynamic_tariff_shadow", "sensor.tou_shadow", "sensor.flex_event_offer",
        "sensor.upgrade_advisor", "sensor.shadow_scorecard", "sensor.battery_soh", "sensor.load_profile",
        "sensor.solar_forecast_p50", "sensor.data_health", "sensor.battery_runtime", "sensor.pv_string_share",
        "sensor.import_causes_today", "binary_sensor.grid_outage", "binary_sensor.inverter_fault",
        "sensor.kpi_avoidable_import", "sensor.kpi_export_peak", "sensor.kpi_self_sufficiency",
        "sensor.kpi_unused_charge", "sensor.kpi_over_limit_import", "switch.away_mode",
    ):
        assert entity in blob, f"{entity} missing from dashboard"


def test_only_documented_custom_cards(dashboard):
    custom = {c["type"] for c in _cards(dashboard) if str(c["type"]).startswith("custom:")}
    assert custom == {"custom:sunsynk-optimizer-flow-card", "custom:apexcharts-card"}


def test_actions_use_perform_action_and_confirm_writes(dashboard):
    blob = json.dumps(dashboard)
    assert "call-service" not in blob
    for card in _cards(dashboard):
        action = card.get("tap_action") or {}
        if action.get("perform_action", "").startswith("sunsynk_optimizer."):
            assert action.get("confirmation"), f"{action['perform_action']} has no confirmation"


def test_markdown_templates_are_balanced(dashboard):
    for card in _cards(dashboard):
        if card["type"] == "markdown":
            text = card["content"]
            assert text.count("{{") == text.count("}}"), text[:120]
            assert text.count("{%") == text.count("%}"), text[:120]


def test_rendered_file_has_no_surrogate_escapes(dashboard):
    # json.dumps' default escapes emoji (e.g. a green circle) as \\ud83d\\udfe2; HA's YAML loader then fails
    # with "surrogates not allowed" and the whole dashboard shows "Unknown error".
    text = conftest._dashboard._render(dashboard)
    assert "\\ud8" not in text.lower() and "\\ud9" not in text.lower()
    assert "\U0001f7e2" in text  # emoji written as real characters
    assert json.loads(text) == dashboard
    text.encode("utf-8")


def test_apexcharts_cards_have_no_unknown_keys(dashboard):
    # apexcharts-card validates its config strictly ("value.grid_options is extraneous" turns the card into an error).
    allowed = {"type", "header", "graph_span", "span", "now", "apex_config", "series", "yaxis"}
    charts = [c for c in _cards(dashboard) if c["type"] == "custom:apexcharts-card"]
    assert len(charts) == 3
    for chart in charts:
        assert set(chart) <= allowed, set(chart) - allowed


def _flow_card(dashboard):
    return next(c for c in _cards(dashboard) if c["type"] == "custom:sunsynk-optimizer-flow-card")


def test_flow_card_config_points_at_real_entities(dashboard):
    card = _flow_card(dashboard)
    ents = card["entities"]
    assert ents["grid_power"].endswith("_grid_pac") and ents["battery_soc"].endswith("_battery_soc")
    assert card["extras"]["target_soc"] == "sensor.current_soc_target"
    assert card["extras"]["forecast"] == "sensor.energy_production_today"


def test_flow_card_file_defines_element_and_registers():
    from pathlib import Path

    js = (Path(__file__).parent.parent / "custom_components" / "sunsynk_optimizer" / "www"
          / "sunsynk-optimizer-flow-card.js").read_text("utf-8")
    assert 'const TAG = "sunsynk-optimizer-flow-card"' in js and "customElements.define(TAG" in js
    assert "window.customCards" in js
    # Every config key the generator writes is read by the card.
    for key in _flow_card_keys():
        assert f"e.{key}" in js or f"x.{key}" in js or f"_cfg.{key}" in js, key


def _flow_card_keys():
    card = _flow_card(conftest._dashboard._build_dashboard({"inverter_serial": "1", "plant_id": "2"}))
    return [k for sect in ("entities", "extras") for k in card[sect]]
