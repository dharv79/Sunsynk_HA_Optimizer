"""Phase 127: daytime, export-pause and peak fixes from the 21/11/2026 code review that aren't
HA-free testable as pure planning logic — pinned here by inspecting the optimizer's own source
(same technique as test_code_integrity.py, test_monitor_guards.py and test_overnight_review_fixes.py)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
OPTIMIZER_FILES = [PKG / "optimizer.py", *sorted(PKG.glob("opt_*.py"))]


def _functions():
    for f in OPTIMIZER_FILES:
        for fn in ast.walk(ast.parse(f.read_text())):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield f.name, fn


def _source(name: str) -> str:
    for _file, fn in _functions():
        if fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found")


def _all_source() -> str:
    return "\n".join(f.read_text() for f in OPTIMIZER_FILES)


def test_periodic_check_tallies_but_the_soc_listener_does_not():
    assert "tally=True" in _source("_async_periodic_flux2_check")
    soc_src = _source("_async_do_battery_soc_changed")
    assert "async_run_flux2_check()" in soc_src and "tally=True" not in soc_src
    decide_src = _source("_async_flux2_decide")
    assert "if tally and cost_trigger" in decide_src


def test_export_pause_start_is_stamped_on_a_fresh_pause_only():
    src = _source("_async_push_flux2_action")
    assert "export_pause_started" in src and "export_paused_date" in src
    assert "not self.coordinator.state.evening_export_disabled" in src


def test_no_trigger_branch_restores_after_the_hold_and_only_clears_on_success():
    src = _source("_async_flux2_decide")
    assert "export_pause_restore_due" in src and "export_pause_clear_slot" in src
    # The flag stays True (not cleared) while the hold hasn't been up for 30 minutes yet.
    assert "no_trigger_pause_holding" in src
    holding_idx = src.index("no_trigger_pause_holding")
    assert src.index("export_pause_restore_due") < holding_idx < src.index("evening_export_disabled=True", holding_idx - 50)
    # export_pause_started is only cleared inside the `if api_ok:` of the restore push.
    assert "if api_ok:" in src and "export_pause_started=None" in src
    assert src.index("if api_ok:") < src.index("export_pause_started=None")


def test_no_trigger_branch_keeps_the_action_while_a_push_still_holds_flux2():
    src = _source("_async_flux2_decide")
    assert "flux2_push_still_active" in src
    assert "if not push_active:" in src


def test_trim_pushes_clamp_their_end_time_to_19_00():
    src = _source("_async_flux2_decide")
    assert src.count("trim_slot_end_minutes") == 1
    soc_src = _source("_async_do_battery_soc_changed")
    assert "trim_slot_end_minutes" in soc_src


def test_normal_trim_gate_uses_the_plans_full_day_flag():
    src = _source("_async_flux2_decide")
    assert "trim_full_day_active" in src
    assert "not trim_full_day and soc > 85" in src.replace("  ", " ")


def test_hold_and_trim_branch_still_use_the_weekday_comparison():
    """Phase 68 says only the 99.5% hold-and-trim branch is unchanged."""
    src = _source("_async_do_battery_soc_changed")
    assert 'now_local.strftime(\'%A\') == self.selected_full_charge_day' in src
    assert "trim_full_day_active" not in src


def test_marginal_cost_uses_the_fixed_reserve_helper():
    src = _source("_async_refresh_marginal_cost")
    assert "reserve_hours_to_offpeak" in src
    assert "24 * 60" not in src


def test_spare_solar_returns_none_shape_without_soc():
    src = _source("_refresh_spare_solar")
    assert "if soc is None" in src
    idx = src.index("if soc is None")
    assert "'total_kwh': None" in src[idx:idx + 200].replace('"', "'")


def test_old_peak_window_tracker_is_gone():
    src = _all_source()
    assert "_async_track_peak_window_usage" not in src
    assert "_peak_window_start" not in src


def test_peak_window_usage_logged_at_19_00_from_snapshots():
    src = _source("_async_meter_snapshot")
    assert '"19:00"' in src.replace("'", '"') and "_async_log_peak_window_usage" in src
    log_src = _source("_async_log_peak_window_usage")
    assert "peak_window_usage_from_snapshots" in log_src


def test_peak_cover_retries_a_failed_push_before_logging():
    src = _source("async_run_peak_cover")
    assert "peak_cover_retry_due" in src
    assert src.index("peak_cover_retry_due") < src.index("state['logged'] = True")


def test_peak_cover_and_export_exclude_away_days():
    for name in ("async_run_peak_cover", "async_run_peak_export"):
        src = _source(name)
        assert "exclude_away_days" in src, name
        assert "async_load_paired_days" in src, name


def test_peak_cover_end_restores_the_plan_when_the_hold_was_carried():
    src = _source("_async_peak_cover_end")
    assert "carried" in src
    assert src.index("if carried:") > src.index("if held:")


def test_reassert_hold_floor_checks_the_slot_still_matches():
    src = _source("_async_reassert_hold_floor")
    assert "daytime_hold_slot" in src


def test_push_flux2_action_marks_a_carried_hold_slot():
    src = _source("_async_push_flux2_action")
    assert "cover['carried'] = True" in src.replace('"', "'")


def test_peak_export_is_outage_aware_and_floored():
    src = _source("async_run_peak_export")
    assert "peak_export_skip_reason(slots_held, soc, outage)" in src
    assert "peak_export_floor_soc" in src
    assert "self._flux2_lock" in src and "_async_peak_cover_end" in src
    assert src.index("async with self._flux2_lock") < src.index("_async_peak_cover_end")


def test_peak_export_recheck_is_outage_aware_and_floored():
    src = _source("async_run_peak_export_recheck")
    assert "_outage_active()" in src
    assert "peak_export_floor_soc" in src


def test_day_actuals_logs_export_paused_date_not_the_live_flag():
    src = _source("_async_do_capture_day_actuals")
    assert "self.coordinator.state.export_paused_date == date" in src
    assert "self.coordinator.state.evening_export_disabled" not in src


def test_cancel_free_event_restore_is_locked():
    src = _source("async_cancel_free_event")
    assert "async with self._flux2_lock" in src
    assert src.index("async with self._flux2_lock") < src.index("_async_restore_normal_plan")
