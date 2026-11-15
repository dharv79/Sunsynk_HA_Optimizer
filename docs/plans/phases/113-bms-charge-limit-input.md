# Phase 113 — Cold-battery charge input from the BMS limit

**Status:** Done, merged (ffb4201, PR #225), shadow only. **Size:** S-M, ~35k actual. **Origin:** efficiency review, 15/11/2026.

## Goal

Size the overnight charge with the live BMS charge-current limit, not only the fixed temperature bands in `planning.temp_deration_factor`.

## Why (evidence)

`resolve_used_charge_rate` derates the charge rate with fixed steps: ×0.85 at 10-15°C, ×0.70 at 5-10°C, ×0.55 at 5°C or below. These are guesses. The BMS reports its real limit (`DeyeBatChargeLimitCurrent`), which phase 75 already reads as `bms_charge_limit_a` for the battery health sensor. The overnight plan does not use it.

When the guess is too low, the Flux 1 window is longer than it needs to be. When the guess is too high, the battery misses its target, and the shortfall is made up later at the day rate. Battery temperature in the plan falls back to 20°C when the sensor is missing, so on cold nights the plan can be wrong.

## Design

- Pure `planning.bms_limited_rate_kw(limit_a, battery_v, inverter_max_a)` returns `min(limit_a, inverter_max_a) × battery_v / 1000`, or `None` when an input is missing. Use 90 A for this inverter, and nominal 51.2 V when no voltage is read.
- In `resolve_used_charge_rate`, when a fresh BMS limit is present (read at the 01:55 plan), use `min(rate, bms_rate)` in place of the band factor. When it is missing, keep the bands. Log `bms_charge_limit_a`, `bms_rate_kw` and `rate_source` (`bms` / `bands` / `measured`) in the plan record. Add them to `_IMPORT_PLAN_FIELDS` (rule 10).
- The limit at 01:55 can differ from the limit during charging, as the cells warm. So ship in shadow first: log both rates, and add a scorecard row comparing each one with the measured rate (phase 61) at 05:00. Then add a `bms_rate_live` option.
- The phase 61 measured rate keeps priority when it is available at tonight's temperature.

## Saving

Unproven. Small in mild weather. On cold nights it avoids a target shortfall (day-rate top-up at 25-28p) or a window held open longer than needed.

## Acceptance

- Pure logic in `planning/overnight.py` with tests (rule 19). Missing data is `None`, never 0.
- Shadow first, with the live behaviour behind an option. No new writes.
- Docs (`import-plan.md`), the plan index and `testing.md` updated.
- py_compile, pyflakes and pytest pass.

## Build notes

- Shadow by default; option `bms_rate_live` (off) makes the BMS rate live. Plan record logs `bms_charge_limit_a`, `bms_rate_kw`, `bms_rate_live`, `rate_source`, `bms_shadow_window_end`.
- The limit comes from a battery-health snapshot under an hour old, refreshed first except on a dry run.
- Not built: the scorecard row comparing each rate with the measured 05:00 rate. Compare the logged `bms_rate_kw` with `measured_charge_rate_kw` before enabling the option.
