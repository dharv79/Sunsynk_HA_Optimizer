"""Tests for the Octopus daily_cost logging added for annual-bill tracking.

daily_cost is captured at 06:00, tagged with the PRIOR day's date (Octopus's
"previous accumulative cost" sensors settle a few hours after midnight, so
reading them at 22:00 the same day would be too early). _pair_records joins
it in by date like every other record type, and computes net_cost_gbp
(import cost - export income + gas cost), None if any of the three inputs
is missing so a partially-configured install doesn't get a misleading total.
"""

from __future__ import annotations

import json

from conftest import _data_logger


def _base_records(date: str):
    return [
        {"type": "import_plan", "date": date, "solar_forecast_kwh": 20.0, "target_soc": 55, "soc": 40},
        {"type": "day_actuals", "date": date, "evening_soc": 67.0, "actual_solar_kwh": 30.2, "evening_export_disabled": False},
    ]


def test_pair_records_carries_daily_cost_and_computes_net():
    dl = object.__new__(_data_logger.DataLogger)
    records = _base_records("2026-08-19") + [
        {
            "type": "daily_cost",
            "date": "2026-08-19",
            "actual_import_cost_gbp": 3.20,
            "actual_export_income_gbp": 1.10,
            "actual_gas_cost_gbp": 2.50,
        },
    ]
    paired = dl._pair_records(records)
    assert len(paired) == 1
    day = paired[0]
    assert day["actual_import_cost_gbp"] == 3.20
    assert day["actual_export_income_gbp"] == 1.10
    assert day["actual_gas_cost_gbp"] == 2.50
    # net = import - export + gas = 3.20 - 1.10 + 2.50
    assert day["net_cost_gbp"] == 4.60


def test_pair_records_net_cost_is_none_when_any_input_missing():
    dl = object.__new__(_data_logger.DataLogger)
    records = _base_records("2026-08-19") + [
        {
            "type": "daily_cost",
            "date": "2026-08-19",
            "actual_import_cost_gbp": 3.20,
            "actual_export_income_gbp": None,   # e.g. no export sensor configured
            "actual_gas_cost_gbp": 2.50,
        },
    ]
    paired = dl._pair_records(records)
    assert paired[0]["actual_import_cost_gbp"] == 3.20
    assert paired[0]["net_cost_gbp"] is None


def test_pair_records_no_daily_cost_record_reads_none():
    # A day with no Octopus data at all (sensors never configured) should
    # read every cost field as None rather than raise or default to 0 (which
    # would look like a real zero-cost day).
    dl = object.__new__(_data_logger.DataLogger)
    paired = dl._pair_records(_base_records("2026-06-01"))
    day = paired[0]
    assert day["actual_import_cost_gbp"] is None
    assert day["actual_export_income_gbp"] is None
    assert day["actual_gas_cost_gbp"] is None
    assert day["net_cost_gbp"] is None


# --------------------------------------------------------------------------- #
# async_log_daily_cost write path                                              #
# --------------------------------------------------------------------------- #

def _make_dl(tmp_path):
    dl = object.__new__(_data_logger.DataLogger)
    dl._data_dir = str(tmp_path)
    return dl


def _read_jsonl(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def test_daily_cost_dedups_same_day(tmp_path):
    # daily_cost is in _DEDUP_TYPES: a second same-day write must not duplicate.
    dl = _make_dl(tmp_path)
    record = {
        "type": "daily_cost",
        "date": "2026-08-18",
        "actual_import_cost_gbp": 3.20,
        "actual_export_income_gbp": 1.10,
        "actual_gas_cost_gbp": 2.50,
    }
    dl._write_record(record)
    dl._write_record(dict(record, actual_import_cost_gbp=99.0))
    records = _read_jsonl(tmp_path)
    assert len(records) == 1
    assert records[0]["actual_import_cost_gbp"] == 3.20  # first write wins
