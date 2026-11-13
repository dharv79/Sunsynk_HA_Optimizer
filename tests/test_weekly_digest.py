"""Phase 104: weekly digest — import causes, charge shortfalls, forecast error, verdict changes, KPI sensors."""

START, END = "2026-11-06", "2026-11-12"


def _cause(date, **kw):
    return {"type": "import_causes", "date": date, "grid_import_kwh": 10.0, **kw}


def test_import_causes_sum_and_price_per_day(planning):
    records = [
        _cause("2026-11-06", grid_charge=6.0, slot_hold=2.0, unknown=1.0),
        _cause("2026-11-07", grid_charge=4.0),
        _cause("2026-11-01", grid_charge=99.0),  # outside the week
    ]
    days = [
        {"date": "2026-11-06", "actual_import_cost_gbp": 2.0, "day_grid_import_kwh": 10.0},  # 0.20/kWh
        {"date": "2026-11-07", "actual_import_cost_gbp": 1.0, "day_grid_import_kwh": 5.0},   # 0.20/kWh
    ]
    out = planning.weekly_import_causes(records, days, START, END)
    assert out["days"] == 2 and out["kwh"] == {"grid_charge": 10.0, "slot_hold": 2.0, "unknown": 1.0}
    assert out["total_kwh"] == 13.0 and out["grid_import_kwh"] == 20.0
    assert out["gbp"] == {"grid_charge": 2.0, "slot_hold": 0.4, "unknown": 0.2} and out["priced_days"] == 2


def test_import_causes_unpriced_is_none_not_zero(planning):
    out = planning.weekly_import_causes([_cause("2026-11-06", grid_charge=3.0)], [], START, END)
    assert out["gbp"] is None and out["priced_days"] == 0 and out["total_kwh"] == 3.0


def test_import_causes_none_without_records(planning):
    assert planning.weekly_import_causes([], [], START, END) is None
    assert planning.weekly_import_causes([_cause("2026-10-01")], [], START, END) is None


def test_last_cause_record_for_a_date_wins(planning):
    records = [_cause("2026-11-06", grid_charge=1.0), _cause("2026-11-06", grid_charge=5.0)]
    assert planning.weekly_import_causes(records, [], START, END)["kwh"] == {"grid_charge": 5.0}


def _outcome(date, target, reached, cause=None):
    return {"type": "charge_outcome", "date": date, "target_soc": target, "reached_soc": reached,
            "shortfall_pct": round(target - reached, 1), "likely_cause": cause}


def test_charge_shortfalls_lists_misses_and_causes(planning):
    records = [
        _outcome("2026-11-06", 100, 87, "cold_derate"),
        _outcome("2026-11-07", 100, 96, "cold_derate"),
        _outcome("2026-11-08", 100, 99.0, "taper"),   # 1 point: not a miss
        _outcome("2026-11-09", 80, 82),               # overshoot
    ]
    out = planning.weekly_charge_shortfalls(records, START, END)
    assert out["nights"] == 4 and [m["date"] for m in out["missed"]] == ["2026-11-06", "2026-11-07"]
    assert out["worst_pct"] == 13.0 and out["causes"] == {"cold_derate": 2}


def test_charge_shortfalls_none_without_nights(planning):
    assert planning.weekly_charge_shortfalls([], START, END) is None


def test_forecast_error_raw_vs_corrected(planning):
    days = [
        {"raw_forecast_kwh": 10.0, "solar_forecast_kwh": 8.0, "actual_solar_kwh": 6.0},
        {"raw_forecast_kwh": 4.0, "solar_forecast_kwh": 3.0, "actual_solar_kwh": 5.0},
        {"raw_forecast_kwh": None, "solar_forecast_kwh": 3.0, "actual_solar_kwh": 5.0},  # incomplete: skipped
    ]
    out = planning.weekly_forecast_error(days)
    assert out["days"] == 2
    assert out["raw_mae_kwh"] == 2.5 and out["corrected_mae_kwh"] == 2.0
    assert out["raw_bias_kwh"] == 1.5 and out["corrected_bias_kwh"] == 0.0
    assert planning.weekly_forecast_error([]) is None


def test_verdict_changes(planning):
    prev = {"peak_export": "not_yet", "peak_cover": "ready"}
    cur = {"peak_export": "ready", "peak_cover": "ready", "soc_cap_85": "not_yet"}
    assert planning.verdict_changes(prev, cur) == [{"feature": "peak_export", "from": "not_yet", "to": "ready"}]
    assert planning.verdict_changes({}, cur) == []  # first run: baseline only
    assert planning.verdict_changes(None, cur) == []


def test_scorecard_verdicts(planning):
    assert planning.scorecard_verdicts({"a": {"verdict": "ready"}, "b": "junk"}) == {"a": "ready"}
    assert planning.scorecard_verdicts(None) == {}


def test_weekly_kpi_values_and_specs(planning):
    keys = [spec[0] for spec in planning.WEEKLY_KPI_SENSORS]
    assert keys == [k.replace("kpi_", "kpi_week_", 1) for k, *_ in planning.KPI_SENSORS]
    assert all(spec[2].endswith("last week") for spec in planning.WEEKLY_KPI_SENSORS)
    vals = planning.weekly_kpi_values({"week_self_sufficiency_pct": 71.5, "week_avoidable_import_gbp": "x"})
    assert vals["kpi_week_self_sufficiency"] == 71.5
    assert vals["kpi_week_avoidable_import"] is None and vals["kpi_week_unused_charge"] is None
    assert set(planning.weekly_kpi_values(None).values()) == {None}


def test_digest_text(planning):
    digest = {
        "import_causes": {"kwh": {"grid_charge": 10.0, "slot_hold": 2.0}},
        "charge": {"nights": 7, "missed": [1, 2]},
        "forecast": {"raw_mae_kwh": 2.5, "corrected_mae_kwh": 1.5},
        "shadow_changes": [{"feature": "peak_export", "from": "not_yet", "to": "ready"}],
    }
    text = planning.digest_text(digest)
    assert "grid charge 10" in text and "2 of 7 nights" in text and "raw 2.5" in text and "peak_export" in text
    assert planning.digest_text(None) == "" and planning.digest_text({}) == ""
