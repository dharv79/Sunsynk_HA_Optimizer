# Phase 91 — Log read performance

**Status:** Done, merged (8e9ad86, PR #201). **Size:** M, ~50-80k est. **Origin:** code review 11/11/2026.

## Goal

Cut the repeated parsing of the monthly JSONL log and move the slow join off the event loop.

## Findings

- `_pair_records` runs on the event loop and calls `snapshots_for_date(records, date)` per date, which rescans every record. Synthetic 395-day log (30k records): 0.5-0.6 s on a fast machine; a Pi will be several times slower. Callers: every plan run (395 days), 06:00 and 22:00 (366 days), monthly reports.
- No read cache. One 01:55 plan parses the same 30-day window five times plus a 395-day read (about 24-29 file parses). The 22:00 callback parses the current month about fifteen times, counting dedup scans.
- Sunday 18:00 and the monthly reports read the same window twice back to back (weekly summary and AI insight; upgrade advisor; electrification advisor).
- Peak cover re-reads the 05:00 snapshot and 14 days of peak load on every 30-minute tick from 09:00 to 16:00; neither changes after 05:00.
- `mpc_plan` is written 48 times a day and never read back from the log: about two-thirds of all lines.
- Dedup-at-write parses the whole current-month file per write and does not see last month's file after a month roll.

## Design

1. Index snapshots by date in one pass inside `_pair_records` (first record per time wins, as now) and run the pairing inside the executor job.
2. One read per callback: load the longest window once and derive the shorter views by `recorded_at`; pass `paired_days` down instead of re-loading.
3. Memoise the peak-cover inputs per date.
4. Write `mpc_plan` to its own monthly file (add the prefix to the 13-month pruner); check `tools/backtest.py` does not need it.
5. Per-month in-memory dedup index of `(type, date, time)`, also checking the previous month in the first days of a month.

Each step is behaviour-preserving and covered by `test_data_logger.py`; add a test that the paired output is identical before and after step 1.

## Saving

None in £. Removes a half-second-plus event-loop stall on each plan and roughly halves log size.

## As built (12/11/2026)

Steps 1, 4 and 5 as designed. Steps 2 and 3 are replaced by a parsed-file cache in `DataLogger._load_file`, which removes the repeat parses without touching any caller. Details: `docs/architecture/data-logging.md`.
