# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0


"""Data export (phase 109): logged records and a daily summary as CSV text. Pure; file writing is the caller's job."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .redact import redact_values

EXPORT_LEAD_COLUMNS = ("date", "time")
SUMMARY_FILE = "daily_summary"
EXPORT_DEFAULT_DAYS = 90  # the range when no start date is given


def record_date(rec: dict[str, Any]) -> str | None:
    """The day a record belongs to: its own `date`, else the day of `recorded_at`; None when neither is usable."""
    date = rec.get("date")
    if isinstance(date, str) and len(date) >= 10:
        return date[:10]
    when = rec.get("recorded_at")
    return when[:10] if isinstance(when, str) and len(when) >= 10 else None


def export_rows(
    records: list[dict[str, Any]], allowed: tuple[str, ...] | list[str], start: str | None, end: str | None,
    types: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """{record type: rows in date order} for ``types`` (default all ``allowed``) dated within [start, end].

    Unknown types are ignored. A row is the record minus `type`; `date` is always filled.
    """
    wanted = [t for t in (types or allowed) if t in allowed]
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in wanted}
    for rec in records:
        kind = rec.get("type")
        if kind not in out:
            continue
        day = record_date(rec)
        if day is None or (start and day < start) or (end and day > end):
            continue
        row = {k: v for k, v in rec.items() if k != "type"}
        row["date"] = day
        out[kind].append(row)
    for rows in out.values():
        rows.sort(key=lambda r: (r["date"], str(r.get("time") or r.get("recorded_at") or "")))
    return {t: rows for t, rows in out.items() if rows}


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return "" if value is None else value


def to_csv(rows: list[dict[str, Any]], sensitive: list[str] | None = None) -> str:
    """CSV text for ``rows`` (columns: date, time, then the rest sorted); nested values become compact JSON.

    Every cell is masked for the sensitive values (phase 93), so an identifier inside a note or a nested value is not exported.
    """
    keys = {k for row in rows for k in row}
    columns = [c for c in EXPORT_LEAD_COLUMNS if c in keys] + sorted(keys - set(EXPORT_LEAD_COLUMNS))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        cells = [_cell(row.get(c)) for c in columns]
        writer.writerow(redact_values(cells, sensitive) if sensitive else cells)
    return buffer.getvalue()


def daily_summary_rows(
    paired_days: list[dict[str, Any]], kpi_rows: list[dict[str, Any]], start: str | None, end: str | None
) -> list[dict[str, Any]]:
    """One row per day joining the paired day (plan, actuals, morning, cost) with that day's KPIs (columns `kpi_*`)."""
    kpis = {r["date"]: r for r in kpi_rows}
    out = []
    for day in sorted(paired_days, key=lambda d: str(d.get("date"))):
        date = str(day.get("date") or "")
        if not date or (start and date < start) or (end and date > end):
            continue
        row = {k: v for k, v in day.items()}
        for key, value in (kpis.get(date) or {}).items():
            if key not in ("date", "time", "recorded_at"):
                row[f"kpi_{key}"] = value
        out.append(row)
    return out
