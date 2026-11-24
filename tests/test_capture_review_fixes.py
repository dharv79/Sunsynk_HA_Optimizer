"""Phase 129: opt_capture.py fixes from the 21/11/2026 code review that aren't HA-free testable as
pure planning logic — pinned by inspecting the integration's own source (same technique as
test_code_integrity.py, test_monitor_guards.py and test_config_entities_review_fixes.py)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"


def _function_source(path: Path, name: str) -> str:
    for fn in ast.walk(ast.parse(path.read_text())):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found in {path}")


# --------------------------------------------------------------------------- #
# Year-to-date / latest-complete cost must not require a plan+actuals pair     #
# --------------------------------------------------------------------------- #

def test_cost_days_from_records_does_not_require_a_plan_or_actuals_record():
    """cost_days_from_records must key off daily_cost alone — the plan/actuals intersection
    (data_logger._pair_records) exists for drain/nudge learning, not for cost."""
    src = _function_source(PKG / "opt_capture.py", "cost_days_from_records")
    assert "r.get('type') == 'daily_cost'" in src
    body = src[src.index('"""', src.index('"""') + 3) + 3 :]  # past the docstring, which names the join
    assert "import_plan" not in body and "day_actuals" not in body


def test_cost_days_from_records_keeps_the_last_daily_cost_record_per_date():
    src = _function_source(PKG / "opt_capture.py", "cost_days_from_records")
    assert "by_date[r['date']] = r" in src


def test_year_to_date_cost_uses_cost_days_from_records_not_the_paired_join():
    """_async_capture_daily_cost must feed year-to-date / latest-complete cost from
    cost_days_from_records(async_load_records(...)), not async_load_paired_days — a day
    whose plan or actuals capture failed must still count towards cost."""
    src = _function_source(PKG / "opt_capture.py", "_async_capture_daily_cost")
    assert "async_load_paired_days" not in src
    assert "cost_days_from_records(await self.data_logger.async_load_records(366))" in src
    assert "latest_complete_cost_day(cost_days" in src
    assert "sum_field(year_days, 'net_cost_gbp')" in src


# --------------------------------------------------------------------------- #
# 22:00 sub-captures must be independently guarded                            #
# --------------------------------------------------------------------------- #

def test_guarded_returns_the_wrapped_call_result_not_always_none():
    """_guarded must hand back the coroutine's result (or None on failure) so a capture step
    that both needs guarding and returns a value (e.g. the day KPIs record) can use either,
    instead of every guarded call being forced to discard its result."""
    src = _function_source(PKG / "optimizer.py", "_guarded")
    assert "result = await coro_factory()" in src and "return result" in src
    assert "return None" in src


def test_day_actuals_log_is_guarded():
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_day_actuals")
    i = src.index("await self._guarded(lambda: self.data_logger.async_log_day_actuals(")
    assert src.index("'Day actuals log'") > i


def test_day_kpis_capture_is_guarded():
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_day_actuals")
    assert "kpi_rec = await self._guarded(lambda: self._async_capture_day_kpis(date), 'Day KPIs capture')" in src


def test_battery_floor_review_is_guarded():
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_day_actuals")
    assert (
        "floor_rec = await self._guarded(lambda: self._async_review_battery_floor(date), 'Battery floor review')"
        in src
    )


def test_hourly_profile_log_is_guarded():
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_day_actuals")
    assert (
        "hourly_rec = await self._guarded(lambda: self._async_log_hourly_profile(date), 'Hourly profile log')"
        in src
    )


def test_guarded_sub_captures_do_not_abort_the_debug_bundle():
    """Every one of the previously-unguarded steps must now go through _guarded, so an
    exception in any one of them can't take the later steps or the notify_debug bundle with
    it; last_day_actuals is only set after all of them have had a chance to run."""
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_day_actuals")
    guarded_calls = src.count("self._guarded(")
    assert guarded_calls >= 9  # auto away, day actuals log, daily cost, kpis, load vs expected,
    # battery floor, hourly profile, pv strings, api health, daily health
    bundle_at = src.index("last_day_actuals=actuals_rec")
    kpis_at = src.index("_async_capture_day_kpis")
    assert kpis_at < bundle_at


# --------------------------------------------------------------------------- #
# Unavailable MPPT must read as None, not a fake low-solar zero               #
# --------------------------------------------------------------------------- #

def test_morning_pv_power_is_none_when_either_mppt_is_unavailable():
    """An unavailable MPPT must not silently sum to 0 — a genuine 0 W reading and "sensor
    unavailable" are otherwise indistinguishable, and the drain-learning gate treats <200 W
    as a confirmed-negligible-solar reading that a fake 0 would wrongly pass."""
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_morning_state")
    assert "mppt0 is None or mppt1 is None" in src
    assert "else mppt0 + mppt1" in src
    # the old pattern summed _state_float(..., 0) unconditionally — must be gone
    assert "_state_float(self.pv_mppt0_entity" not in src
    assert "_state_float(self.pv_mppt1_entity" not in src


def test_morning_pv_power_dashboard_value_uses_round_or_none():
    src = _function_source(PKG / "opt_capture.py", "_async_do_capture_morning_state")
    assert '"morning_pv_power": round_or_none(pv_power, 1)' in src.replace("'", '"')
