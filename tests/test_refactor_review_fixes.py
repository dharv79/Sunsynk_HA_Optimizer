"""Phase 131: behaviour-preserving refactors from the 21/11/2026 code review that aren't HA-free
testable as pure planning logic — pinned by inspecting the integration's own source (same
technique as test_daytime_review_fixes.py and test_monitor_guards.py)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"


def _module_source(path: Path) -> str:
    return path.read_text()


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def _method_source(path: Path, class_name: str, method_name: str) -> str:
    tree = ast.parse(path.read_text())
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == class_name)
    for fn in cls.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == method_name:
            return ast.unparse(fn)
    raise AssertionError(f"{class_name}.{method_name} not found in {path}")


def test_sensor_registration_and_unit_lookup_share_one_table():
    """SunsynkOptimizerSensor used to spread name/category/entity_id/unit/measurement-eligibility
    across the registration list, a separate unit if/elif chain and a separate measurement set —
    now all in one _SENSOR_SPECS table, so a new sensor can't drift between them."""
    src = _module_source(PKG / "sensor.py")
    assert "_MEASUREMENT_KEYS" not in src
    assert re.search(r"_SENSOR_SPECS\s*:\s*dict", src)

    init_src = _method_source(PKG / "sensor.py", "SunsynkOptimizerSensor", "__init__")
    assert "_SENSOR_SPECS[sensor_key]" in init_src

    unit_src = _method_source(PKG / "sensor.py", "SunsynkOptimizerSensor", "native_unit_of_measurement")
    assert "_SENSOR_SPECS[self._sensor_key].get('unit')" in unit_src.replace('"', "'")

    setup_src = _function_source(PKG / "sensor.py", "async_setup_entry")
    assert "for key in _SENSOR_SPECS" in setup_src


def test_import_plan_splits_off_shadow_fields_and_rate_and_window():
    """Phase 131: async_run_import_plan's ~375 lines split into the main function plus two named
    pieces — the phase 64/70/82 shadow-only forecast fields, and the reviewer's "rate and window
    glue" (effective/used/BMS-limited charge rate and the Flux 1 window sized from it)."""
    main_src = _function_source(PKG / "opt_overnight.py", "async_run_import_plan")
    assert "_async_shadow_forecast_fields(" in main_src
    assert "_async_rate_and_window(" in main_src
    # async_run_import_plan no longer computes these inline — they come back from the helpers.
    for leaked in ("compute_effective_charge_rate_kw", "bms_limited_rate_kw", "newsvendor_shadow"):
        assert leaked not in main_src, leaked

    rate_window_src = _function_source(PKG / "opt_overnight.py", "_async_rate_and_window")
    assert "compute_effective_charge_rate_kw" in rate_window_src
    assert "bms_limited_rate_kw" in rate_window_src
    assert "gentle_charge_current_a" in rate_window_src

    shadow_src = _function_source(PKG / "opt_overnight.py", "_async_shadow_forecast_fields")
    assert "_newsvendor_shadow(" in shadow_src
    assert "forecast_quantiles" in shadow_src


def test_free_event_timers_are_decided_by_the_planning_timer_plan():
    """Phase 131: _arm_free_event_timers' which-timers-for-which-phase logic, previously untested
    inline, is now the pure, tested free_event_timer_plan; the mixin just dispatches its names to
    literal self.attr callbacks (so test_code_integrity's coroutine check still applies)."""
    src = _function_source(PKG / "opt_events.py", "_arm_free_event_timers")
    assert "free_event_timer_plan(" in src
    for name, callback in (
        ("watch_start", "_async_free_event_watch_start"),
        ("free_start", "_async_free_event_free_start"),
        ("free_end", "_async_free_event_free_end"),
    ):
        assert f"'{name}' in plan" in src.replace('"', "'"), name
        assert f"self.{callback}" in src, callback


def test_sensor_specs_cover_every_registered_sensor_with_a_name():
    """Every row has a name (pin_entity_id needs it to fall back to a slug) — values besides "name"
    can't be literal_eval'd (EntityCategory.DIAGNOSTIC is an attribute, not a literal)."""
    tree = ast.parse(_module_source(PKG / "sensor.py"))
    specs_dict = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "_SENSOR_SPECS"
    ).value
    assert len(specs_dict.keys) >= 30
    for key_node, spec_node in zip(specs_dict.keys, specs_dict.values):
        name_value = next(
            v for k, v in zip(spec_node.keys, spec_node.values)
            if isinstance(k, ast.Constant) and k.value == "name"
        )
        assert isinstance(name_value, ast.Constant) and name_value.value, ast.literal_eval(key_node)
