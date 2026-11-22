"""Phase 125: monitor-mode backstop, push results that follow the push, dry-run skips, full-day trim timing."""

from __future__ import annotations

import ast
import re
from datetime import date
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


def test_reset_refusal(planning):
    assert "Monitor mode" in planning.reset_refusal("monitor", False)
    assert "free event" in planning.reset_refusal("auto", True)
    assert planning.reset_refusal("auto", False) is None


def test_setting_text_drops_the_trailing_zero(planning):
    assert planning.setting_text(60.0) == "60"
    assert planning.setting_text(39) == "39"
    assert planning.setting_text(52.5) == "52.5"
    assert planning.setting_text("85") == "85"


def test_full_trim_waits_for_the_peak(planning):
    hour = planning.FULL_TRIM_HOLD_S
    assert planning.full_trim_delay_s(5 * 60) == 11 * 3600  # full at 05:00: trims at 16:00
    assert planning.full_trim_delay_s(15 * 60 + 30) == hour  # full at 15:30: an hour's hold
    assert planning.full_trim_delay_s(17 * 60) == hour


def test_monitor_mode_expects_no_import_plan(planning):
    assert "import_plan" in planning.audit_daily_types()
    assert "import_plan" not in planning.audit_daily_types(monitor=True)
    recs = [{"type": t, "date": "2026-11-20"} for t in ("morning_state", "day_actuals", "day_kpis")]
    assert planning.records_today(recs, "2026-11-20", planning.audit_daily_types(True)) == (3, 3)
    issues = planning.audit_days(
        recs + [{"type": "day_kpis", "date": "2026-11-19"}], date(2026, 11, 21), 1,
        daily_types=planning.audit_daily_types(True),
    )
    assert not [i for i in issues if i["type"] == "import_plan"]


def test_monitor_health_ignores_a_stale_push(planning):
    base = dict(
        version="2.3.8", api_ok=False, charge_shortfall_pct=None, records_written=3, records_expected=3,
        guarded_errors={}, api_failures=0, soc_age_hours=0.2, shadow_features=[],
    )
    assert planning.daily_health(**base)["status"] == "Problem"
    out = planning.daily_health(**{**base, "api_ok": None}, monitor=True)
    assert out["status"] == "OK" and out["plan_pushed"] is None


def test_push_helpers_refuse_in_monitor_mode():
    """Rule 16 backstop: both write helpers check the mode before calling the API."""
    for name in ("_async_post_with_status", "_async_write_setting"):
        src = _source(name)
        assert src.index("_monitor_refuses") < src.index("try:"), name


def test_monitor_writes_only_undo_our_own():
    """allow_in_monitor is passed only by paths that undo this integration's own write."""
    allowed = {
        "async_push_flux_override", "_async_restore_normal_plan", "async_restore_charge_current",
        "_async_apply_hold_floor", "_async_peak_cover_end",
    }
    users = set()
    for _file, fn in _functions():
        for node in ast.walk(fn):
            if isinstance(node, ast.keyword) and node.arg == "allow_in_monitor":
                users.add(fn.name)
    assert users <= allowed, users - allowed


def test_reset_button_follows_the_push():
    src = _source("async_reset_flux_baseline")
    assert "reset_refusal" in src
    assert re.search(r"'evening_export_disabled': False\} if ok else \{\}", src)


def test_export_pause_flag_follows_the_push():
    assert "evening_export_disabled if api_ok else" in _source("_async_push_flux2_action")


def test_full_day_trim_respects_monitor_mode():
    src = _source("_async_do_battery_soc_changed")
    assert src.count("self.operation_mode == 'monitor'") >= 2
    assert "full_trim_delay_s" in src


def test_dry_run_skip_changes_no_state():
    """Rule 17: a dry-run skip returns before last_error or the warning."""
    src = _source("_async_skip_plan")
    assert src.index("if dry_run:") < src.index("last_error")
    plan = _source("async_run_import_plan")
    assert plan.count("self._async_skip_plan(") == 2
    assert plan.count("dry_run, notify") == 2


def test_drift_check_reads_return_none_on_failure_not_empty():
    """Phase 129: a failed read-back is None, distinct from [] ('genuinely checked, no drift')."""
    flux_src = _source("_async_read_flux_drift")
    assert "return None" in flux_src
    current_src = _source("_async_read_current_drift")
    assert "return None" in current_src


def test_drift_check_does_not_clear_episode_on_a_failed_read():
    """A None from either read must short-circuit before the 'no diffs -> clear' branch."""
    src = _source("async_run_drift_check")
    assert "flux_diffs is None or current_diffs is None" in src
    assert src.index("is None or current_diffs is None") < src.index("if not diffs:")


def test_inverter_fault_uses_last_known_health_on_a_failed_read():
    """A failed workdata read must fall back to the last stored snapshot, not drop the fault flag."""
    src = _source("async_check_inverter_events")
    assert "last_health = health if health is not None else" in src
    assert "health_fault_flag(last_health)" in src


def test_tariff_alert_ignores_a_wholly_unavailable_octopus_read():
    """All bands None (entity unavailable, not unconfigured) must leave any existing alert alone."""
    src = _source("async_run_tariff_price_check")
    assert "all((v is None for v in octopus.values()))" in src
    assert src.index("all((v is None for v in octopus.values()))") < src.index("tariff_price_changes(")


def test_periodic_drift_interval_has_its_own_callback():
    """The 2-hourly interval must not share the post-push one-shot handle (opt_monitor.py:316)."""
    periodic_src = _source("_async_periodic_drift_check")
    assert "_drift_check_unsub" not in periodic_src
    one_shot_src = _source("_async_drift_check")
    assert "self._drift_check_unsub = None" in one_shot_src
    setup_src = _source("async_setup")
    assert "self._async_periodic_drift_check, timedelta(hours=DRIFT_CHECK_INTERVAL_HOURS)" in setup_src


def test_morning_brief_release_keeps_notifications_held_mid_send():
    """Phase 129: only the items the brief actually included are dropped, not everything held since."""
    src = _source("_async_do_release_held")
    assert "held_notifications=[]" not in src
    assert "[len(held):]" in src
