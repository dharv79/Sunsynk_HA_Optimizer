"""Tests for the day_actuals load/grid fields added for consumption analysis.

evening_soc alone conflates household load with solar availability, which made
it impossible to cleanly separate "high load" days from "low solar" days when
reviewing whether avg_consumption_kw needed adjusting. day_load_kwh (the
SolarSynkV3 daily load total) and the grid import/export totals are logged
directly so that analysis doesn't have to be inferred from the SOC swing.
"""

from __future__ import annotations

import json

from conftest import _data_logger


def test_pair_records_carries_load_and_grid_fields():
    dl = object.__new__(_data_logger.DataLogger)
    records = [
        {
            "type": "import_plan",
            "date": "2026-08-01",
            "recorded_at": "2026-08-01T01:55:00+00:00",
            "solar_forecast_kwh": 20.0,
            "target_soc": 55,
            "soc": 40,
        },
        {
            "type": "day_actuals",
            "date": "2026-08-01",
            "recorded_at": "2026-08-01T22:00:00+00:00",
            "evening_soc": 67.0,
            "actual_solar_kwh": 30.2,
            "evening_export_disabled": False,
            "day_load_kwh": 11.4,
            "day_grid_import_kwh": 2.1,
            "day_grid_export_kwh": 4.8,
        },
    ]
    paired = dl._pair_records(records)
    assert len(paired) == 1
    day = paired[0]
    assert day["day_load_kwh"] == 11.4
    assert day["day_grid_import_kwh"] == 2.1
    assert day["day_grid_export_kwh"] == 4.8


def test_pair_records_missing_load_fields_is_none():
    # Records written before this release won't have the new fields; they
    # should read as None rather than raise, so old history still pairs.
    dl = object.__new__(_data_logger.DataLogger)
    records = [
        {"type": "import_plan", "date": "2026-06-01", "solar_forecast_kwh": 20.0, "target_soc": 55, "soc": 40},
        {"type": "day_actuals", "date": "2026-06-01", "evening_soc": 67.0, "actual_solar_kwh": 30.2, "evening_export_disabled": False},
    ]
    paired = dl._pair_records(records)
    assert paired[0]["day_load_kwh"] is None
    assert paired[0]["day_grid_import_kwh"] is None
    assert paired[0]["day_grid_export_kwh"] is None
    assert paired[0]["overnight_load_kwh"] is None


def test_pair_records_carries_overnight_load_kwh():
    dl = object.__new__(_data_logger.DataLogger)
    records = [
        {"type": "import_plan", "date": "2026-08-01", "solar_forecast_kwh": 20.0, "target_soc": 55, "soc": 40},
        {"type": "day_actuals", "date": "2026-08-01", "evening_soc": 67.0, "actual_solar_kwh": 30.2, "evening_export_disabled": False},
        {"type": "morning_state", "date": "2026-08-01", "morning_soc": 45.0, "morning_pv_power": 20.0, "overnight_load_kwh": 4.7},
    ]
    paired = dl._pair_records(records)
    assert paired[0]["overnight_load_kwh"] == 4.7


# --------------------------------------------------------------------------- #
# peak_window_usage write path (16:00-19:00 diagnostic record)                 #
# --------------------------------------------------------------------------- #

def _make_dl(tmp_path):
    dl = object.__new__(_data_logger.DataLogger)
    dl._data_dir = str(tmp_path)
    return dl


def _read_jsonl(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def test_peak_window_usage_is_written_with_expected_shape(tmp_path):
    dl = _make_dl(tmp_path)
    dl._write_record({
        "type": "peak_window_usage",
        "date": "2026-08-18",
        "peak_load_kwh": 3.2,
        "peak_grid_import_kwh": 1.1,
        "peak_grid_export_kwh": 0.0,
    })
    records = _read_jsonl(tmp_path)
    assert len(records) == 1
    assert records[0]["type"] == "peak_window_usage"
    assert records[0]["peak_load_kwh"] == 3.2


def test_peak_window_usage_dedups_same_day(tmp_path):
    # peak_window_usage is in _DEDUP_TYPES: a second same-day write (e.g. from
    # a restart re-triggering the >=19:00 branch) must not create a duplicate.
    dl = _make_dl(tmp_path)
    record = {
        "type": "peak_window_usage",
        "date": "2026-08-18",
        "peak_load_kwh": 3.2,
        "peak_grid_import_kwh": 1.1,
        "peak_grid_export_kwh": 0.0,
    }
    dl._write_record(record)
    dl._write_record(dict(record, peak_load_kwh=9.9))  # different payload, same type+date
    records = _read_jsonl(tmp_path)
    assert len(records) == 1
    assert records[0]["peak_load_kwh"] == 3.2  # first write wins, no duplicate
