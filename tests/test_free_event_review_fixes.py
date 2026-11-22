"""Phase 130: free-electricity-event fixes from the 21/11/2026 code review (checked after the
22/11/2026 live run) that aren't HA-free testable as pure planning logic — pinned here by
inspecting the optimizer's own source (same technique as test_daytime_review_fixes.py and
test_overnight_review_fixes.py)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
OPTIMIZER_FILES = [PKG / "optimizer.py", PKG / "__init__.py", *sorted(PKG.glob("opt_*.py"))]


def _source(name: str) -> str:
    for f in OPTIMIZER_FILES:
        for fn in ast.walk(ast.parse(f.read_text())):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return ast.unparse(fn)
    raise AssertionError(f"{name} not found")


def test_event_transitions_run_under_the_flux2_lock():
    """A restart after free_end, or any other overlap, can no longer write back a stale copy
    of the event (the race that wrapped Flux 1 to ~23 h and left one stuck in 'charging')."""
    for name in ("_async_do_free_event_sell_start", "_async_do_free_event_free_start", "_async_do_free_event_free_end"):
        src = _source(name)
        assert "async with self._flux2_lock:" in src, name


def test_manual_scheduling_is_refused_in_monitor_mode():
    """Do-not-break 16: the dashboard entry point now refuses like every other service entry
    point, so a monitor-mode event can never reach the allow_in_monitor restore push."""
    src = _source("async_try_schedule_manual_free_event")
    assert "self._service_guard()" in src


def test_restore_push_result_drives_the_finished_and_cancelled_notifications():
    """Do-not-break 5: 'Normal plan restored'/'cancelled' is no longer sent when the push failed."""
    restore_src = _source("_async_restore_normal_plan")
    assert "return await self.async_push_flux_override" in restore_src

    end_src = _source("_async_do_free_event_free_end")
    assert "restore_ok = await self._async_restore_normal_plan()" in end_src
    assert "if restore_ok else" in end_src

    cancel_src = _source("async_cancel_free_event")
    assert "restore_ok = await self._async_restore_normal_plan()" in cancel_src
    assert "if restore_ok else" in cancel_src


def test_failed_sell_or_free_start_push_retries_before_advancing():
    """A failed push now retries (free_event_push_retry_due) instead of moving the phase on
    as if it had landed."""
    sell_src = _source("_async_do_free_event_sell_start")
    assert "free_event_push_retry_due(attempts)" in sell_src
    assert sell_src.index("free_event_push_retry_due") < sell_src.index("phase'] = 'selling'")

    start_src = _source("_async_do_free_event_free_start")
    assert "free_event_push_retry_due(attempts)" in start_src
    assert start_src.index("free_event_push_retry_due") < start_src.index("phase'] = 'charging'")


def test_schedule_service_validates_times_and_surfaces_refusal():
    """The schedule_free_event service no longer accepts end before/at start or a start in the
    past, and no longer reports success when async_schedule_free_event refuses."""
    service_src = _source("async_service_schedule_free_event")
    assert "free_event_schedule_error" in service_src
    assert "raise HomeAssistantError(error)" in service_src

    handler_src = _source("_schedule")
    assert "if not await" in handler_src and "raise HomeAssistantError" in handler_src
