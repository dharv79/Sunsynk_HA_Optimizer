# Phase 86 — Daytime import and reserve-hold review

**Status:** Built (10/11/2026), shadow only; was planned, low priority (10/11/2026 review: likely about £0 in winter). **Size:** S, ~25-40k est. **Origin:** Octopus statement 9 Oct-9 Nov 2026 and `#sunsynkdebug` review (10/11/2026). Overlaps phases 62, 64 and 84; re-read them first.

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

## Review note (10/11/2026)

Outside 16:00-19:00 every hour is the same day rate, so a kWh of battery displaces the same 25-28p whether it is used in the morning, afternoon or evening. The reserve hold therefore costs nothing while the battery is cycled fully each day (unused charge 0.0 kWh, floor reached by 22:00). The 5 Nov-9 Nov day-rate import is battery capacity, not scheduling. Build only if the scorecard shows a day where the hold left charge unused at 02:00 or a peak import that cover would have avoided.

## Built (10/11/2026)

- The 30-min tick (no new listener) and the 22:00 capture add timed samples of SOC, `grid_etoday_from` and `load_daily_used` to an unpersisted day buffer (max 120). 22:00 logs one `hourly_profile` record per day (deduped): `hourly` rows `{hour, soc, grid_import_kwh, load_kwh}` plus the split below. Samples are not on the hour, so `planning.hourly_profile` credits each step to the earlier sample's hour; resets and missing values give `None`.
- `planning.reserve_hold_cost(hourly, floor, prices)` splits import into day-rate with charge above the floor (+3 points), day-rate at the floor, and peak. Night-window import is ignored. Bands are the fixed Flux hours (night 02-05, peak 16-19, else day).
- Shadow scorecard `reserve_hold`: days with at least 0.5 kWh of above-floor day-rate import. No £ counterfactual. Per the review note, build any change to the hold only if those days appear.
- Floor used is the code's `BATTERY_LOW_SOC` (25), not the phase 84 read-back.
