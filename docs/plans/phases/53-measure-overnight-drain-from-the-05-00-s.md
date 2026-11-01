# Phase 53 — Measure overnight drain from the 05:00 snapshot SOC, not target_soc

**Status:** Planned, not built. **Size:** S. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Measure the drain correction as soc at 05:00 (what the charge actually reached, from the meter_snapshot) minus morning_soc at 06:00, with target_soc as the fallback. Then an incomplete charge is no longer counted as drain and the adjustment stops sitting at its +20 cap.

## Why (evidence)

data_logger.compute_overnight_drain_adjustment documents 'target_soc − morning_soc' and takes a p75 capped at 20. On target-100 nights the charge stopped short (morning SOC 94-98), and that shortfall is learned as 'drain'. The drain buffer was then added again on bridge nights, for example 25 Oct, which ended with 2.2 kWh of unused charge. The 05:00 meter_snapshot SOC has been logged since phase 14.

## Design

Pair the 05:00 snapshot SOC onto paired days in data_logger._pair_records, adding soc_05 to _IMPORT_PLAN_FIELDS or to the pairing logic per do-not-break 10. In _is_drain_night/compute_overnight_drain_adjustment, use soc_05 − morning_soc when it is present and target − morning otherwise, and log both values (drain_source). Run tools/backtest.py with the new drain before switching. Ship as log-both for 2 weeks, then switch behind a flag. Tests in tests/test_data_logger.py.

## Value

About 5-15 points less target on bridge nights, which is roughly 0.5-1.5 kWh less off-peak charge that later goes unused. That saves about £0.05-0.20 per such night plus fewer cycles, roughly £10-20/yr in the shoulder months.

## Risk

The 05:00-06:00 load is real drain, so check that removing the charge shortfall doesn't under-buffer appliance nights. Keep the p75.

## Vetting note

Confirmed in data_logger.py:506-564. The drain is computed against target_soc, and _is_drain_night accepts any initial<target night.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
