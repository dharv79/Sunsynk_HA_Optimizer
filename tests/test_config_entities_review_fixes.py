"""Phase 128: config/entities/diagnostics/card fixes from the 21/11/2026 code review that aren't
HA-free testable as pure planning logic — pinned by inspecting the integration's own source (same
technique as test_code_integrity.py, test_monitor_guards.py and test_overnight_review_fixes.py)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"

# A field whose blank value is meant to mean "off", not "reset to a compiled default": clearing it
# must leave the key genuinely absent from the saved options (rule: "blank = off" sticks).
BLANK_IS_OFF_TEXT_FIELDS = (
    "CONF_FLEX_EVENT_ENTITY",
    "CONF_AUTO_AWAY_PERSONS",
    "CONF_AUTO_AWAY_CALENDAR",
    "CONF_HOURLY_FORECAST_SENSOR",
    "CONF_OCTOPUS_IMPORT_COST_SENSOR",
    "CONF_OCTOPUS_EXPORT_INCOME_SENSOR",
    "CONF_OCTOPUS_GAS_COST_SENSOR",
    "CONF_OCTOPUS_IMPORT_RATES_ENTITY",
    "CONF_OCTOPUS_EXPORT_RATES_ENTITY",
    "CONF_OCTOPUS_IMPORT_CONSUMPTION_SENSOR",
    "CONF_OCTOPUS_EXPORT_CONSUMPTION_SENSOR",
    "CONF_TARIFF_RATE_OVERRIDES",
    "CONF_DATA_REPORT_TARGET",
    "CONF_QUIET_START",
    "CONF_QUIET_END",
    "CONF_MORNING_BRIEF_TIME",
)


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def test_blank_is_off_text_fields_use_suggested_value_not_default():
    """HA's FlowManager re-validates a submitted form against the schema that rendered it: a
    vol.Optional(key, default=OLD_VALUE) reinserts the old value when the frontend omits a cleared
    field, so the options flow's own "pop if missing from user_input" clearing logic never runs.
    description={"suggested_value": ...} pre-fills the form without that silent backfill."""
    src = _function_source(PKG / "config_flow.py", "_base_schema")
    for name in BLANK_IS_OFF_TEXT_FIELDS:
        start = src.index(f"vol.Optional({name}")
        end = src.index(")", src.index("Selector()", start))
        clause = src[start:end]
        assert "description={'suggested_value'" in clause.replace('"', "'"), name
        assert f"default={name}" not in clause and "default=values" not in clause, name


def test_options_init_step_still_drops_a_field_missing_from_user_input():
    src = _function_source(PKG / "config_flow.py", "async_step_init")
    assert "key.schema not in user_input" in src and "_working.pop" in src


def test_merge_entry_data_deep_copies_the_options_dict():
    src = _function_source(PKG / "flux_helpers.py", "merge_entry_data")
    assert "deepcopy(options)" in src.replace(" ", "")


def test_monitor_mode_adaptive_sensors_do_not_fall_back_to_the_placeholder_default():
    """last_import_plan is stubbed to {"logic_branch": "monitor_only", ...} in monitor mode
    (opt_overnight.async_run_import_plan), so the adaptive-sensor default must not stand in for a
    real measurement forever — only before the very first plan, outside monitor mode."""
    src = (PKG / "sensor.py").read_text()
    i = src.index("if self._sensor_key in _ADAPTIVE_PLAN_KEYS")
    clause = src[i : src.index("if self._sensor_key in _ADAPTIVE_THRESHOLDS", i)]
    assert "operation_mode=='monitor'" in clause.replace(" ", "").replace('"', "'")
    assert "return None" in clause


def test_monitor_mode_stub_matches_the_sensor_guard():
    src = _function_source(PKG / "opt_overnight.py", "async_run_import_plan")
    assert "logic_branch" in src and "monitor_only" in src


def test_settings_audit_redacts_changes_before_notifying():
    src = _function_source(PKG / "opt_monitor.py", "async_run_settings_audit")
    assert "redact_settings_changes" in src
    redact_at = src.index("redact_settings_changes")
    notify_at = src.index("async_notify(")
    debug_at = src.index("async_notify_debug(")
    assert redact_at < notify_at and redact_at < debug_at


def test_heatmap_card_defers_custom_element_definition():
    src = (PKG / "www" / "sunsynk-optimizer-heatmap.js").read_text()
    assert 'customElements.whenDefined("home-assistant")' in src
    assert src.index("const define = ()") < src.index("customElements.define(TAG, SunsynkOptimizerHeatmap)")


def test_heatmap_card_keeps_its_resize_observer_across_a_detach():
    src = (PKG / "www" / "sunsynk-optimizer-heatmap.js").read_text()
    disconnected = src[src.index("disconnectedCallback() {") : src.index("_build() {")]
    assert "_ro" not in disconnected


def test_day_chart_resamples_by_local_time_of_day_not_elapsed_ms():
    src = (PKG / "www" / "sunsynk-optimizer-day-chart.js").read_text()
    assert "getHours() * 60 + d.getMinutes()" in src
    assert "function resample(points, dayStart, dayEnd, nowMs)" in src


def test_initial_setup_error_codes_all_have_a_translation():
    """config_flow._validate_input can set these errors on the initial `user` step too (every field
    it checks is also on that step's schema): the config.error block must carry a translation for
    each, not just the options.error block."""
    src = (PKG / "config_flow.py").read_text()
    codes = set(re.findall(r'errors\[[^\]]+\] = "(\w+)"', src))
    codes |= set(re.findall(r'errors\.setdefault\([^,]+, "(\w+)"\)', src))
    expected = {"required", "invalid_time", "quiet_pair"}
    assert expected <= codes, codes  # every code _validate_input can set on the `user`/`init` steps
    for fname in ("strings.json", "translations/en.json"):
        data = json.loads((PKG / fname).read_text())
        have = set(data["config"]["error"])
        assert expected <= have, f"{fname} missing {expected - have}"
        assert "same_time" in have  # time_field_errors' third code; harmless to carry even if unused here
