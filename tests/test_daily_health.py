"""Phase 100: daily health verdict, records-today count, deploy changes, record-type registry."""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"

CLEAN = dict(
    version="2.0.5", api_ok=True, charge_shortfall_pct=0.0, records_written=4, records_expected=4,
    guarded_errors={}, api_failures=0, soc_age_hours=0.2, shadow_features=["mpc_plan"],
)


def test_clean_day_is_ok(planning):
    out = planning.daily_health(**CLEAN)
    assert out["status"] == "OK" and out["reasons"] == []
    assert out["version"] == "2.0.5" and out["shadow_features"] == ["mpc_plan"]


def test_failed_push_and_stale_soc_are_problems(planning):
    assert planning.daily_health(**{**CLEAN, "api_ok": False})["status"] == "Problem"
    assert planning.daily_health(**{**CLEAN, "soc_age_hours": 7})["status"] == "Problem"


def test_check_conditions(planning):
    for change in (
        {"guarded_errors": {"Plan": 2}},
        {"api_failures": 1},
        {"charge_shortfall_pct": 9.0},
        {"records_written": 3},
        {"soc_age_hours": 3.0},
    ):
        assert planning.daily_health(**{**CLEAN, **change})["status"] == "Check", change


def test_small_shortfall_is_fine(planning):
    assert planning.daily_health(**{**CLEAN, "charge_shortfall_pct": 4.0})["status"] == "OK"


def test_missing_core_input_is_never_ok(planning):
    for key in ("api_ok", "soc_age_hours", "records_written", "records_expected"):
        assert planning.daily_health(**{**CLEAN, key: None})["status"] is None, key


def test_missing_charge_outcome_does_not_block_ok(planning):
    """No charge_outcome record (e.g. no overnight charge) is not a fault."""
    assert planning.daily_health(**{**CLEAN, "charge_shortfall_pct": None})["status"] == "OK"


def test_problem_outranks_check(planning):
    out = planning.daily_health(**{**CLEAN, "api_ok": False, "api_failures": 2})
    assert out["status"] == "Problem" and len(out["reasons"]) == 2


def test_records_today_counts_distinct_daily_types(planning):
    recs = [
        {"type": "import_plan", "date": "2026-11-13"},
        {"type": "import_plan", "date": "2026-11-13"},
        {"type": "day_actuals", "date": "2026-11-13"},
        {"type": "day_kpis", "date": "2026-11-12"},
    ]
    assert planning.records_today(recs, "2026-11-13") == (2, len(planning.AUDIT_DAILY_TYPES))


def test_deploy_changes(planning):
    assert planning.deploy_changes(["a", "b"], ["a", "b", "c", "d"]) == ["c", "d"]
    assert planning.deploy_changes(["a"], ["a"]) == []
    assert planning.deploy_changes([], ["a"]) is None
    assert planning.deploy_changes(None, ["a"]) is None


def test_record_type_registry_covers_every_written_type():
    src = (PKG / "data_logger.py").read_text()
    registry = set(re.search(r"RECORD_TYPES = \((.*?)\)", src, re.S).group(1).replace('"', "").replace(",", " ").split())
    written = set(re.findall(r'_record\(\s*"([a-z_0-9]+)"', src))
    assert written <= registry
    dedup = set(re.search(r"_DEDUP_TYPES = \((.*?)\)", src, re.S).group(1).replace('"', "").replace(",", " ").split())
    assert dedup <= registry
