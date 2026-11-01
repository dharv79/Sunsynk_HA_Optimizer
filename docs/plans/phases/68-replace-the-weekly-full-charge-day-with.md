# Phase 68 — Replace the weekly full-charge day with 'days since the last 100%'

**Status:** Planned, not built. **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Skip the forced weekly 100% charge when the battery has already reached ≥99% (from solar or a low-solar override) in the last N days. Force one only when that count runs out.

## Why (evidence)

Between 26 and 31 Oct the target was 100 on 6 of 7 nights from low-solar overrides. The weekly_full_charge_day on 27 Oct added nothing, and then 15.2 kWh of solar arrived with the battery at 95% and 8.7 kWh was exported. In summer, the forced 100% buys about 5 extra kWh off-peak on a day when solar would fill the battery anyway. The briefing notes this as weakness 8 ('a days-since-last-100% rule would be simpler'). The Sunday pick plus the 18:00 re-check (phase 19 choice) can stay as the fallback date.

## Design

Add planning.full_charge_due(last_full_date, today, max_gap_days=7). Read last_full_date from the max SOC in the existing 16:00/19:00/22:00 meter snapshots and morning_state. In select_target_soc, treat is_full_charge_day as False when it is not due, and record the reason 'recent_full_skip'. Shadow-log the skip in import_plan for 4 weeks before it goes live. Test in tests/test_planning*.

## Value

Removes about 20-30 forced full charges a year in spring and summer, each about 3-5 kWh of extra off-peak import that later displaces solar export. That is about 10-20p net per skip, ~£4-8/yr, plus around 100 kWh/yr less time at high SOC, which helps LFP calendar life.

## Risk

If the full-charge day is there for cell balancing, a 99% SOC reading may not mean the cells balanced. Keep a hard 14-day maximum gap.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
