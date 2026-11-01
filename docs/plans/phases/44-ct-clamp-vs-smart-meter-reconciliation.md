# Phase 44 — CT clamp vs smart-meter reconciliation

**Status:** Done, merged (9fd7115, PR #90). **Size:** XS. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Once Octopus settles a day, compare the inverter's grid import and export (`day_actuals.day_grid_import_kwh/_export_kwh`) with the sum of the smart meter's 48 half-hourly slots. Alert when they diverge persistently, which points to a misplaced or failing CT clamp or a meter or billing fault.

## Why (evidence)

Both data sets are already logged and never compared. `day_actuals` stores the inverter's grid meters (data_logger.py:217–237). `_async_capture_half_hourly` (optimizer.py:2520) logs the settled Octopus `half_hourly_import`/`_export`. Every live trigger trusts the CT: the export pause on grid_pac, trims and over-limit import. Odd figures such as 0.0% self-sufficiency on 25 Oct are what a CT fault would produce.

## Design

Add `planning.meter_reconciliation(inverter_kwh, octopus_slots, tolerance_pct)` with tests. Call it from `_async_capture_half_hourly` after each settled date is logged. Write a per-date deduped `meter_reconciliation` record (add it to `_DEDUP_TYPES`). Notify after 3 consecutive days more than about 8% apart. Add the ratio to the weekly digest JSON. Missing either side gives `None`, never 0.

## Value

Protects the accuracy of every other feature. A 5% meter over-read at this import level is about £20–40/yr in billing. No new inputs are needed.

## Risk

The day boundaries must line up (Octopus slots vs the inverter's local midnight reset, BST). Octopus settlement lags by days, so compare only settled dates.

## Vetting note

Confirmed both sources: day_actuals grid fields in data_logger.py and the Octopus half-hourly capture in optimizer.py:2520. No comparison exists. It is the cheapest high-confidence item on the list.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built (01/11/2026)

- `planning.meter_reconciliation` compares the 22:00 inverter total with slots 00:00–22:00 only (44 slots), diverged = gap ≥ 0.5 kWh and ≥ 8%; `planning.divergence_streak` counts trailing consecutive diverged dates.
- Records `meter_reconciliation_import` / `_export` (per date, deduped) written from `_async_reconcile_meters`, called at the end of `_async_capture_half_hourly`. One notification when the streak reaches exactly 3 days.
- Not done: weekly digest ratio (deferred).
