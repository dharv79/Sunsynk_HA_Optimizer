# Phase 11 — Latest complete cost day in the 22:00 bundle

**Status:** Built (PR pending). **Size:** XS–S, ~20-40k est. Verify: next 22:00 `#sunsynkdebug` post carries a complete-day record with a non-null `net_cost_gbp`.

## Scope

The 22:00 debug bundle's `daily_cost` line almost never shows import, gas or net cost, so the stream looks like cost data is missing.

## Diagnosis (confirmed from live sensor attributes, 06/10/2026)

`last_reset` on the Octopus "previous accumulative cost" sensors at 22:20 on 06/10:

| Sensor | Reports | Lag |
|---|---|---|
| Import | 04/10 | 2 days |
| Export | 05/10 | 1 day |
| Gas | 04/10 | 2 days |

Lag varies (import was 1 day behind on 02–03/10). `_read_octopus_previous_day_cost` dates each reading correctly and `async_merge_daily_cost` back-fills up to `_OCTOPUS_CATCH_UP_DAYS` (5), so the log is complete — the weekly summary's `days_with_cost_data` confirms it. But `last_daily_cost` (what the bundle and `sensor.consumption` show) tracks the **newest** date, which on this account only has export at 22:00. Net cost is `None` there by design (do-not-break 11).

Not a fix: moving the read from 22:00 to 22:10 (lag is days, not seconds).

## Design

- Pure helper in `planning.py`: from recent `daily_cost` records, pick the most recent date with import, export and gas all non-null; return it with `net_cost_gbp`. `None` if none in the window. Gas-not-configured accounts: treat gas as not required (match `net_cost_gbp` semantics — check how a blank gas sensor is handled before deciding).
- Recompute in `_async_capture_daily_cost` after merging (data is already loaded for year-to-date) and store on `OptimizerState` (e.g. `last_complete_daily_cost`) via `update_state`.
- Emit as an extra JSON line in the 22:00 bundle (`"type": "daily_cost_complete"`). Keep the existing `daily_cost` line unchanged.
- Optional: expose as attributes on `sensor.consumption` / dashboard "Net cost" if cheap.

## Acceptance criteria

- Helper unit-tested: all-present, gas-lagging, none-complete, multiple dates (row in `docs/architecture/testing.md`).
- Missing data stays `None`, never 0; no logged value overwritten.
- Existing `daily_cost` line, weekly summary and year-to-date unchanged.
- Feature section added to `docs/architecture/octopus-cost.md`.

## Outcome

Built as designed; `sensor.consumption` attributes not added (optional, skipped). Gas-less accounts: gas required, matching `net_cost_gbp` (they never get a net cost today either).
