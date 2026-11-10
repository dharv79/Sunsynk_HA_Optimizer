# Phase 86 — Daytime import and reserve-hold review

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** Octopus statement 9 Oct-9 Nov 2026 and `#sunsynkdebug` review (10/11/2026). Overlaps phases 62, 64 and 84; re-read them first.

## Evidence

- Statement: 143.8 kWh (35%) of import was at the day rate (about £35 of £96); peak import was only 12.8 kWh.
- Logs 22 Oct-9 Nov (19 days, window totals only): day-rate import 05:00-16:00 averaged 2.3 kWh/day (up to 6.7 in the last 5 days) and 19:00-22:00 averaged 1.2 kWh/day (up to 4.9). The night charge is flat at about 10 kWh (window full) and the battery is at its 25% floor by 22:00 on nearly every recent day.
- On 9 Nov the 16:00-19:00 window used 1.2 kWh while 4.9 kWh was imported 19:00-22:00.

## Question to answer

Is the evening reserve held at 16:00 (about 50%) sized for the right hours, and does holding it make the house buy at the day rate in the morning? Window totals cannot tell, so this phase starts with data.

## Design

- Log an hourly `soc`, `grid_import_kwh` and `load_kwh` record per day (`hourly_profile`, dedup at write; missing → `None`, rules 11 and 13). Extend the existing snapshot times, no new listeners.
- Pure `planning.reserve_hold_cost(hourly, reserve_soc, prices)` returning day-rate import bought while the battery sat above the floor, and evening import after it ran out. Test in `planning.py` (rule 19).
- Shadow only: report in the shadow scorecard; no change to the hold or floor until a month of data supports it.

## Savings estimate

Unproven. Upper bound about £35 a winter from a lower floor (phase 84) plus a smaller amount if the hold is mis-sized; could be £0 if the current trade-off is right.
