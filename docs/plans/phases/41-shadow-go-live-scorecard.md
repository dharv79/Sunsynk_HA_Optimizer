# Phase 41 — Shadow go-live scorecard

**Status:** Done, merged (9eb643f, PR #93). **Size:** M. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

A monthly report and diagnostic sensor that score each shadow feature (15 import feedback, 17 peak export, 20 weighted correction, 23 gentle charge, 30 peak cover, 34 capacity tracking, 39 multi-day) against what actually happened. Each gets the decisions it would have changed, the £ delta, and a verdict of ready, not yet, harmful or no records, with the evidence dates.

## Why (evidence)

Seven shadow features are waiting for a go-live review, and each one's evidence sits in a different record. Last week's figures: peak-export shadow found 8.1 kWh and £0.67, then logged `no_surplus` on all 7 days this week. `soc_cap_shadow` (a day_kpis field) would have cost £0.13–0.14/day on 30–31 Oct. `multi_day_plan` wrote no records at all this week, which could be a silent bug.

## Design

Add `planning.shadow_scorecard(paired_days, records)` with one scorer per feature and tests. Inputs are `import_plan` feedback fields, the `peak_export` and `peak_cover` records, `day_kpis.soc_cap_shadow`, `multi_day_plan`, the live vs weighted correction factor, and `capacity_estimate`. Treat 'no records in 14 days' as its own verdict. Run it from the monthly branch of the 18:00 dispatcher next to `_async_send_monthly_battery_value`. Send a JSON line to `data_report_target`, a short human summary, and `sensor.shadow_scorecard` with one attribute per feature. It is read-only, so it is safe in monitor mode.

## Value

Turns 7 open decisions into a monthly yes or no. Peak export alone looks worth about £15–35/yr in the shoulder seasons. The report also stops a harmful setting, such as the 85% cap at about £0.13/day in winter, from being switched on, and it surfaces silent shadow failures.

## Risk

Counterfactuals are approximate, so verdict thresholds need to be conservative. It must never auto-enable a `*_live` option.

## Vetting note

Not built: no scorecard or aggregate code exists. The record types `peak_export`, `peak_cover`, `multi_day_plan` and `capacity_estimate` exist in data_logger.py:247–269 and `_DEDUP_TYPES`. `soc_cap_shadow` is a day_kpis field (optimizer.py:2077), not a record, so I corrected that. It directly serves the README open item 'shadow features waiting on go-live review'.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built (01/11/2026)

- `planning.shadow_scorecard(records, today)` scores import feedback (15), peak export (17), weighted correction (20), gentle charge (23), peak cover (30), capacity (34), 85% cap (`day_kpis.soc_cap_shadow`) and multi-day (39) over 30 days. Each returns verdict, days, changed, `delta_gbp`, last 5 evidence dates and a note. `delta_gbp` is `None` where no £ counterfactual exists (import feedback, gentle charge, multi-day).
- Verdicts: `no_records` (nothing logged; peak export and peak cover also when 14 days are silent), `harmful` (delta ≤ -£0.50, or weighted error ≥ 10% worse), `ready` (≥7 days, ≥3 changed, delta > 0; weighted error ≥ 5% better), else `not_yet`.
- Runs on the first Sunday of the month at 18:00 (`_async_send_shadow_scorecard`, after battery value). Sends a `shadow_scorecard` JSON line to the debug target, a short human summary, and sets `sensor.shadow_scorecard` (state = number of ready features, attributes per feature). Read-only: no API writes, safe in monitor mode, never changes a `*_live` option.
- Tests: `tests/test_planning.py` (phase 41 section).
