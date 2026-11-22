"""Phase 129: opt_reports.py fixes from the 21/11/2026 code review that aren't HA-free testable
as pure planning logic — pinned by inspecting the optimizer's own source (same technique as
test_overnight_review_fixes.py)."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer" / "opt_reports.py").read_text()
TREE = ast.parse(SRC)


def _source(name: str) -> str:
    for fn in ast.walk(TREE):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found")


def test_scorecard_record_load_covers_the_capacity_window():
    # The capacity feature inside shadow_scorecard looks back CAPACITY_SCORECARD_WINDOW_DAYS (70),
    # so both callers must load at least that many days of records, not just SCORECARD_WINDOW_DAYS + 2.
    for name in ("_async_weekly_digest", "_async_send_shadow_scorecard"):
        src = _source(name)
        assert "CAPACITY_SCORECARD_WINDOW_DAYS" in src, name


def test_monthly_reports_retry_per_report_not_as_a_block():
    src = _source("_async_monthly_reports")
    assert "monthly_reports_sent" in src
    # A report already sent this month must not be resent...
    assert "sent.get(key) == report_month" in src
    # ...and the month is only marked fully done once every report has succeeded.
    assert "all_sent" in src and "self._MONTHLY_REPORT_KEYS" in src


def test_bill_check_guards_none_gap_per_day_pct():
    src = _source("async_run_bill_reconciliation").replace('"', "'")
    # A billed amount of £0 makes gap_per_day_pct None even when coverage is fine; formatting it
    # unconditionally with :+.1f used to raise TypeError.
    assert "result['gap_per_day_pct'] is not None" in src


def test_bill_reconciliation_counts_cost_days_not_only_paired_days():
    src = _source("async_run_bill_reconciliation")
    assert "async_load_cost_days" in src
    assert "async_load_paired_days" not in src


def test_monthly_battery_value_window_reaches_the_start_of_the_report_month():
    src = _source("_async_send_monthly_battery_value")
    assert "days=40" not in src
    assert "history_days" in src


def test_monthly_senders_no_longer_carry_a_dead_force_gate():
    """Phase 131: _async_monthly_reports is the only caller of either, and it always wants the
    report sent (the catch-up IS the point), so the `force`-gated day<=7 check never fired."""
    for name in ("_async_send_monthly_battery_value", "_async_send_monthly_tariff_comparison"):
        src = _source(name)
        assert "force" not in src, name
        assert "today.day > 7" not in src, name
        assert "last_calendar_month(today)" in src, name


def test_weekly_cost_summary_and_ai_insight_use_the_fresh_digest_only():
    for name in ("_async_send_weekly_cost_summary", "_async_send_ai_weekly_insight"):
        src = _source(name)
        assert "_fresh_weekly_digest()" in src
        assert "state.weekly_digest" not in src, name


def test_export_data_builds_csvs_inside_the_executor_job():
    src = _source("async_service_export_data")
    before, marker, after = src.partition("def _write(")
    assert marker, "no _write helper found"
    # to_csv (incl. redaction) must happen inside _write, run via async_add_executor_job, not
    # synchronously on the event loop before the executor job is dispatched.
    assert "to_csv" not in before
    assert "to_csv" in after
