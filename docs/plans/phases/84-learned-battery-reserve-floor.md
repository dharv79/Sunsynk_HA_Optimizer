# Phase 84 — Battery low-SOC floor review

**Status:** Built (10/11/2026), read-only; feasibility answered by the first 22:00 log. **Size:** S, ~20-30k est. **Origin:** idea sweep 08/11/2026 (idea 11); reworked 10/11/2026 after the Oct-Nov log review.

## Goal

Find out whether the 25% floor the battery sits at every dark evening is the inverter's own low-battery setting, and show what lowering it would save.

## Evidence (10/11/2026)

- `evening_soc` is exactly 25.0 on 10 of the last 12 days: a hard floor, not a learned house minimum. The code's `BATTERY_BOTTOM_SOC` is 20 and nothing reads the inverter's low-battery setting, so a learned 10th percentile (the original design) would just return 25 and save nothing.
- Usable capacity is about 11.4 kWh (scorecard `capacity`), and the 3 kW night window already fills it on dark days. Each 5 points of floor is about 0.57 kWh a day at roughly 10p (day vs night rate), so 25 → 15% is about £0.11 a day, £15-25 a year on dark days only.

## Design

- Feasibility first: check whether the settings API read-back (`api.py`) exposes the low-battery / shutdown SOC. If not, On hold.
- Read-only. Log `battery_floor_soc` per day, compare with the lowest SOC reached, and report the £ a lower floor would have saved in the weekly summary. Never write the setting (the floor is a safety and warranty choice for the user; rule 16 for monitor mode still applies).
- Pure `planning.floor_saving_gbp(history, floor_soc, candidate_soc, capacity_kwh, prices)` with a test (rule 19). Away days excluded (rule 18); missing → `None` (rule 11).

## Savings estimate

£15-25 a year if the user lowers the inverter setting; £0 from the integration itself.

## Built (10/11/2026)

- 22:00 `battery_floor` record (deduped per day, in the daily data report): `battery_floor_soc`, `floor_key`, `floor_keys` (candidate key names only, no values), `candidate_soc` (15), `window_days`, `hit_days`, `saving_30d_gbp`.
- `planning.battery_floor_soc(settings)` takes the first settings key containing "low" and "cap" with a 0-100 value; `planning.floor_saving_gbp` counts days whose evening SOC sat within 1 point of the floor, skips away days (rule 18) and returns `None` without a floor, capacity or prices (rule 11).
- Feasibility is unverified from the repo: the settings key names are not captured anywhere. If the first record shows `battery_floor_soc` null, `floor_keys` lists what the cloud exposes; if it is empty too, put the phase On hold.
- Deviation: the £ figure is in the daily record, not the weekly summary text.
- Nothing is written to the inverter; monitor mode is unaffected (read-only).
