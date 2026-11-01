# Phase 63 — Remove the 05:00-06:00 double count in drain and bridge

**Status:** Done, merged (b9e8e85, PR #92); median switch deferred. **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Measure drain up to 05:00, which the snapshot now provides, instead of 06:00, or subtract the overlap hour. Then use a median with no round-up, so the overnight target stops stacking drain + bridge + p75 + round-to-5 + nudge.

## Why (evidence)

On 25 Oct the drain adjustment hit its +20 cap. The target was 59, the 22:00 SOC was 64, and 2.2 kWh of charge went unused. The week before shows 2.84 kWh of unused charge. compute_overnight_drain_adjustment measures from the target to 06:00, while the bridge counts load from 05:00 (optimizer.py:989, hours_to_solar = solar start − 5.0), so 05:00-06:00 is counted twice. The p75, rounding up to 5 and the evening nudge then add more on top. The 28 Oct startup re-plan dropped drain from +20 to 0 and still gave a reasonable result, which suggests +20 is inflated.

## Design

In data_logger.compute_overnight_drain_adjustment, take morning SOC from the 05:00 meter_snapshot when it exists, falling back to 06:00 × (window/(window+1)). Switch to the median, rounded to the nearest 5 rather than up, behind a constant. Before switching, replay 30 nights in tools/backtest.py using the existing --target-offset machinery and compare unused_charge_kwh and evening imports. Add data_logger tests.

## Value

About 5-10 SOC points less overnight over-charge on bridge nights, roughly 0.5-1 kWh/night. Each kWh saved is about 17p/0.92 of off-peak charge, plus wear, plus solar that is stored instead of exported. That is ~£15-30/yr, and fewer days with a battery full before 11:00 (overnight_target_too_high).

## Risk

A lower target on a misforecast morning means some 05:00-solar-start import at 27.77p. The backtest must show net savings before the default changes.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built (01/11/2026)

- `planning.drain_pct_to_0500` returns the drain to 05:00: target minus the 05:00 snapshot SOC when it exists, otherwise the 06:00 drain scaled by (window-1)/window (window = Flux 1 end to 06:00; <=1h or unparseable keeps the 06:00 figure; missing is `None`).
- `_pair_records` adds `overnight_drain_pct_0500`; `compute_overnight_drain_adjustment` uses it behind `DataLogger._DRAIN_USE_0500` (True), falling back to the 06:00 drain per night.
- Deliberately NOT changed: p75 stays (`_DRAIN_PERCENTILE`) and rounding stays to nearest 5. No logged nights exist in the repo, so the 30-night backtest could not be run; the median switch needs that backtest first (see Risk). Do it from live logs with `tools/backtest.py --target-offset`.
- Tests: `tests/test_data_logger.py` (phase 63 section).
