"""Tests for phase 133: clearing the dashboard's Last error and finished free-event notices."""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import conftest

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


def test_error_resolved_matches_only_its_own_check(planning):
    assert planning.error_resolved("Flux 2 check skipped — unavailable: sensor.x", "Flux 2 check skipped")
    assert planning.error_resolved("Peak cover failed: boom", "Peak cover failed")
    assert not planning.error_resolved("API push failed: timeout", "Peak cover failed")
    assert not planning.error_resolved(None, "Peak cover failed")
    assert not planning.error_resolved("Peak cover failed: boom", "")


def test_notice_shows_while_live_and_after_until_dismissed(planning):
    for phase in ("scheduled", "selling", "charging"):
        assert planning.free_event_notice_shown({"phase": phase, "dismissed": True})
    assert planning.free_event_notice_shown({"phase": "done"})
    assert planning.free_event_notice_shown({"phase": "cancelled"})
    assert not planning.free_event_notice_shown({"phase": "done", "dismissed": True})
    assert not planning.free_event_notice_shown({})
    assert not planning.free_event_notice_shown(None)


def test_finished_notice_hides_once_its_day_is_over(planning):
    done = {"phase": "done"}
    today = date(2026, 11, 24)
    assert planning.free_event_dismiss_due(done, date(2026, 11, 22), today)
    assert not planning.free_event_dismiss_due(done, today, today)
    assert planning.free_event_dismiss_due({"phase": "cancelled"}, None, today)
    assert not planning.free_event_dismiss_due({"phase": "done", "dismissed": True}, date(2026, 11, 22), today)
    assert not planning.free_event_dismiss_due({"phase": "scheduled"}, date(2026, 11, 22), today)


def test_guarded_clears_its_own_error_on_success():
    src = _function_source(PKG / "optimizer.py", "_guarded")
    assert src.index("_clear_resolved_error(f'{label} failed')") > src.index("return None")


def test_flux2_check_clears_its_skip_error_once_inputs_are_back():
    src = _function_source(PKG / "opt_daytime.py", "_async_flux2_decide")
    assert src.index("_clear_resolved_error(FLUX2_SKIPPED_ERROR)") > src.index("FLUX2_SKIPPED_ERROR}")


def test_clear_notices_keeps_the_event_record():
    src = _function_source(PKG / "opt_events.py", "async_clear_notices")
    assert "'last_error': None" in src and "event['dismissed'] = True" in src
    assert "free_event={}" not in src and "async_push" not in src


def test_dashboard_alert_and_clear_button():
    dashboard = conftest._dashboard._build_dashboard({
        "inverter_serial": "2411132500", "plant_id": "111111",
        "solar_forecast_sensor": "sensor.energy_production_today", "weather_entity": "weather.forecast_home",
    })
    blob = json.dumps(dashboard)
    assert '"entity": "binary_sensor.free_event_notice", "state": "on"' in blob
    assert '"entity": "button.clear_notices"' in blob
    assert '"state_not": "none"' not in blob
