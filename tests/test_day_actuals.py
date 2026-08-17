"""Tests for the day_actuals load/grid fields added for consumption analysis.

evening_soc alone conflates household load with solar availability, which made
it impossible to cleanly separate "high load" days from "low solar" days when
reviewing whether avg_consumption_kw needed adjusting. day_load_kwh (the
SolarSynkV3 daily load total) and the grid import/export totals are logged
directly so that analysis doesn't have to be inferred from the SOC swing.
"""

from __future__ import annotations

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
