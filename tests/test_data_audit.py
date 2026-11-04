"""Phase 45: nightly data-integrity audit (pure functions)."""
from datetime import date

TODAY = date(2026, 11, 4)


def _full(d):
    return [{"type": t, "date": d} for t in ("import_plan", "morning_state", "day_actuals", "day_kpis")]


def test_complete_days_have_no_issues(planning):
    recs = _full("2026-11-02") + _full("2026-11-03")
    assert planning.audit_days(recs, TODAY) == []


def test_missing_type_reported_from_first_record_only(planning):
    recs = _full("2026-11-02") + [r for r in _full("2026-11-03") if r["type"] != "import_plan"]
    issues = planning.audit_days(recs, TODAY)
    assert [(i["kind"], i["type"], i["date"]) for i in issues] == [("missing", "import_plan", "2026-11-03")]


def test_dates_before_first_record_not_flagged(planning):
    assert planning.audit_days(_full("2026-11-03"), TODAY) == []


def test_empty_history_is_quiet(planning):
    assert planning.audit_days([], TODAY) == []


def test_today_is_excluded(planning):
    recs = _full("2026-11-03") + [{"type": "import_plan", "date": "2026-11-04"}]
    assert planning.audit_days(recs, TODAY) == []


def test_duplicate_detected_but_meter_snapshots_at_different_times_ok(planning):
    recs = _full("2026-11-03") + [{"type": "day_kpis", "date": "2026-11-03"}]
    recs += [{"type": "meter_snapshot", "date": "2026-11-03", "time": t} for t in ("02:00", "05:00")]
    issues = planning.audit_days(recs, TODAY)
    assert [(i["kind"], i["type"]) for i in issues] == [("duplicate", "day_kpis")]


def test_contradictions(planning):
    recs = _full("2026-11-03")
    recs[2].update(actual_solar_kwh=13.7, day_load_kwh=0)
    recs[3].update(self_sufficiency_pct=0.0)
    kinds = {(i["kind"], i["type"]) for i in planning.audit_days(recs, TODAY)}
    assert kinds == {("contradiction", "day_kpis"), ("contradiction", "day_actuals")}


def test_zero_ss_with_little_solar_is_fine_and_none_is_not_zero(planning):
    recs = _full("2026-11-03")
    recs[2].update(actual_solar_kwh=2.0)
    recs[3].update(self_sufficiency_pct=0.0)
    assert planning.audit_days(recs, TODAY) == []
    recs[2].update(actual_solar_kwh=13.0)
    recs[3].update(self_sufficiency_pct=None)
    assert planning.audit_days(recs, TODAY) == []


def test_daily_cost_only_when_expected_and_settled(planning):
    recs = _full("2026-11-01") + _full("2026-11-02") + _full("2026-11-03")
    assert planning.audit_days(recs, TODAY) == []
    issues = planning.audit_days(recs, TODAY, expect_cost=True)
    assert {i["date"] for i in issues} == {"2026-11-01", "2026-11-02"}
    recs.append({"type": "daily_cost", "date": "2026-11-01", "actual_import_cost_gbp": 1.2})
    assert {i["date"] for i in planning.audit_days(recs, TODAY, expect_cost=True)} == {"2026-11-02"}


def test_summary_and_redaction(planning):
    issues = [{"kind": "missing", "type": "day_kpis", "date": "2026-11-03", "detail": "x"}]
    assert planning.audit_summary(issues) == {"count": 1, "classes": {"missing": ["2026-11-03 day_kpis"]}}
    out = planning.redact_config({"username": "a", "password": "b", "plant_id": "1", "target": 80, "nested": {"api_token": "z", "n": 1}})
    assert out == {"username": "**REDACTED**", "password": "**REDACTED**", "plant_id": "**REDACTED**", "target": 80, "nested": {"api_token": "**REDACTED**", "n": 1}}
