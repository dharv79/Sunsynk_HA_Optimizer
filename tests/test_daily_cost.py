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
import os

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


_FULL = {"actual_import_cost_gbp": 3.20, "actual_export_income_gbp": 1.10, "actual_gas_cost_gbp": 2.50}


def test_merge_fields_fills_only_unset():
    existing = {"actual_import_cost_gbp": 3.20, "actual_export_income_gbp": 1.10, "actual_gas_cost_gbp": None}
    merged = _data_logger.merge_daily_cost_fields(existing, {"actual_import_cost_gbp": 99.0, "actual_gas_cost_gbp": 2.5})
    assert merged == _FULL  # gas filled, logged import never overwritten


def test_merge_fields_returns_none_when_nothing_new():
    assert _data_logger.merge_daily_cost_fields(_FULL, {"actual_gas_cost_gbp": 9.0}) is None
    assert _data_logger.merge_daily_cost_fields({}, {}) is None


def test_merge_daily_cost_same_values_does_not_duplicate(tmp_path):
    dl = _make_dl(tmp_path)
    assert dl._merge_daily_cost("2026-08-18", _FULL) is not None
    assert dl._merge_daily_cost("2026-08-18", dict(_FULL, actual_import_cost_gbp=99.0)) is None
    records = _read_jsonl(tmp_path)
    assert len(records) == 1
    assert records[0]["actual_import_cost_gbp"] == 3.20  # first value wins


def test_gas_settling_a_day_late_completes_the_day(tmp_path):
    # Electricity for the 18th arrives first; gas for the 18th only on the
    # next read. The superseding record must pair as a complete day.
    dl = _make_dl(tmp_path)
    dl._merge_daily_cost("2026-08-18", {"actual_import_cost_gbp": 3.20, "actual_export_income_gbp": 1.10})
    record = dl._merge_daily_cost("2026-08-18", {"actual_gas_cost_gbp": 2.50})
    assert record["actual_import_cost_gbp"] == 3.20 and record["actual_gas_cost_gbp"] == 2.50
    paired = dl._pair_records(_base_records("2026-08-18") + _read_jsonl(tmp_path))
    assert paired[0]["net_cost_gbp"] == 4.60


def test_merge_finds_record_in_previous_month_file(tmp_path):
    # A catch-up written early in a new month must see the partial record
    # already sitting in the previous month's file.
    from datetime import datetime, timedelta, timezone

    dl = _make_dl(tmp_path)
    last_of_prev = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
    date = last_of_prev.date().isoformat()
    (tmp_path / f"{last_of_prev:%Y-%m}.jsonl").write_text(json.dumps(
        {"type": "daily_cost", "date": date, "actual_import_cost_gbp": 3.2,
         "actual_export_income_gbp": 1.1, "actual_gas_cost_gbp": None}
    ) + "\n")
    record = dl._merge_daily_cost(date, {"actual_import_cost_gbp": 99.0, "actual_gas_cost_gbp": 2.5})
    assert record["actual_import_cost_gbp"] == 3.2
    assert record["actual_gas_cost_gbp"] == 2.5
    assert os.path.exists(dl._current_month_file())
