# Phase 141: Report every Open-Meteo backfill outcome to the debug stream

Status: Done, merged (7b1d9d6d8654fa81b4701be4194f6b965c7fb9b4, PR #267)

## Why

After 2.3.14 was deployed (28/11/2026, 15:59), the startup backfill was due at about 16:02, but nothing reached `#sunsynkdebug`. The phase 138 backfill posts its `om_backfill` line only when the run succeeds. Every other outcome is silent there, so a failed or skipped run looks the same as one that hasn't happened yet:

- The Open-Meteo options (PV kWp, tilt, azimuth) are blank, or HA has no location: it returns with no record at all.
- No archive returns data: the `unavailable` record goes to the log file only.
- It raises: `_guarded` sets **Last error**, but nothing is posted.

## Decisions

User request (28/11/2026), from the 2.3.14 deploy check:

- Read-only reporting. The backfill's training, backtest and once-only behaviour are unchanged.

## What

- **Outcomes:** `async_run_om_backfill` sends one `om_backfill` debug line to `CONF_DATA_REPORT_TARGET` for every outcome. `status` is one of:
  - `ok`;
  - `unavailable` (every archive failed, with the archive names tried);
  - `not_configured` (`reason`: which option or the location is missing);
  - `error` (`reason`: the exception text).
- **Logging:** `not_configured` and `error` also write the `om_backfill` record. Only `ok` sets `om_backfilled`, so a startup run that fails is retried at the next restart.
- **Error handling:** an exception is caught inside the backfill so it can be reported, then raised again so `_guarded` still sets **Last error**.
- **Progress:** a `started` line is posted when a run begins (button or startup), giving the date range, so a long run can be told apart from a missing one.
- **Diagnostics:** the latest backfill status and reason appear in the diagnostics download.

## Verification

- Tests in `tests/test_open_meteo_backfill.py`:
  - each exit path posts one line with the right status;
  - `not_configured` names the missing option;
  - an exception both posts `error` and reaches `_guarded`;
  - only `ok` sets `om_backfilled`.
- Live: press **Backfill solar forecast history**. A `started` line and then one outcome line appear in `#sunsynkdebug`.
