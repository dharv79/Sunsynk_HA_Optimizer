"""Phase 109: CSV export rows, masking and the daily summary join (pure)."""
import csv
import io

TYPES = ("day_actuals", "day_kpis", "import_plan")


def _recs():
    return [
        {"type": "day_actuals", "date": "2026-11-02", "day_load_kwh": 12.5, "recorded_at": "2026-11-02T22:00:00+00:00"},
        {"type": "day_actuals", "date": "2026-11-01", "day_load_kwh": 11.0},
        {"type": "day_actuals", "date": "2026-10-01", "day_load_kwh": 9.0},
        {"type": "day_kpis", "recorded_at": "2026-11-02T22:00:05+00:00", "x": {"a": 1}, "note": "plant 99887766 ok"},
        {"type": "mystery", "date": "2026-11-02"},
        {"type": "import_plan", "date": "2026-11-02", "target_soc": 80},
    ]


def test_rows_filter_by_type_range_and_sort(planning):
    out = planning.export_rows(_recs(), TYPES, "2026-11-01", "2026-11-30")
    assert list(out) == ["day_actuals", "day_kpis", "import_plan"]
    assert [r["date"] for r in out["day_actuals"]] == ["2026-11-01", "2026-11-02"]
    assert out["day_kpis"][0]["date"] == "2026-11-02"  # from recorded_at
    assert "type" not in out["day_actuals"][0]
    only = planning.export_rows(_recs(), TYPES, None, None, ["import_plan", "nope"])
    assert list(only) == ["import_plan"]


def test_csv_columns_nested_and_masking(planning):
    rows = planning.export_rows(_recs(), TYPES, None, None, ["day_kpis"])["day_kpis"]
    text = planning.to_csv(rows, ["99887766"])
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0][0] == "date"
    cells = dict(zip(parsed[0], parsed[1]))
    assert cells["x"] == '{"a":1}'
    assert "99887766" not in text and "**REDACTED**" in cells["note"]


def test_summary_joins_kpis_with_prefix(planning):
    paired = [{"date": "2026-11-02", "target_soc": 80}, {"date": "2026-10-01", "target_soc": 70}]
    kpis = [{"date": "2026-11-02", "import_kwh": 7.0, "recorded_at": "t"}]
    rows = planning.daily_summary_rows(paired, kpis, "2026-11-01", None)
    assert rows == [{"date": "2026-11-02", "target_soc": 80, "kpi_import_kwh": 7.0}]


def test_record_date_fallbacks(planning):
    assert planning.record_date({"date": "2026-11-02T01:00"}) == "2026-11-02"
    assert planning.record_date({"recorded_at": "2026-11-03T01:00:00+00:00"}) == "2026-11-03"
    assert planning.record_date({}) is None
