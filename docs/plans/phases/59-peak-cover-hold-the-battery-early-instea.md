# Phase 59 — Peak cover: hold the battery early instead of recharging it at 13:45

**Status:** Built (behind `peak_cover_live`, off by default). **Size:** M. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

When peak cover first projects a 16:00 shortfall while the SOC is still at or above required_soc, push a hold slot at the required SOC. The grid then runs the house directly at the day rate. Today it waits until the SOC is below the target and then charges it back up at the day rate.

## Why (evidence)

On 31 Oct the SOC was 95 at 05:00 and fell to 40 by 13:45. Peak cover then bought 4.71 kWh at 27.77p over 103 minutes to get back to 73. Every one of those kWh went through a charge loss (CHARGE_EFFICIENCY 0.92) and a battery cycle that holding would have avoided. The import is at the same day rate either way. peak_cover_plan (planning.py:1061) only knows top_up and computes gain_gbp as peak − day/0.92. It has no 'hold' option, even though its docstring says the Flux 1 slot 'holds the battery there'.

## Design

Add decision='hold' to planning.peak_cover_plan. It applies when soc >= required_soc and the projected 16:00 SOC is below required − PEAK_COVER_MIN_SHORTFALL_SOC. It returns target_soc=required_soc with push_now=True. The gain counts the avoided losses: topup_kwh×day×(1/0.92−1) plus wear×topup_kwh. In optimizer.async_run_peak_cover, push the hold through the same Flux 1 now→16:00 slot path and restore it the same way. The 12:00 start becomes configurable (e.g. from 09:00) so the hold can catch the SOC on its way down. Log hold vs top_up in the peak_cover record. Behind peak_cover_live as today, with a test in tests/test_planning*.

## Value

About 0.4 kWh of charge loss plus 4.7 kWh of avoided cycling on a 31 Oct-type day. That is ~11p of loss plus ~25-35p of wear at a typical 5-7p/kWh wear cost, so ~40p per event. Over ~60 winter top-up days that is ~£20-25/yr, plus ~280 kWh/yr less battery throughput.

## Risk

The required SOC is guessed in the morning, so on a sunny afternoon a hold makes the house import early when solar would have covered it. Mitigate by only holding when the raw forecast for the rest of the day is below the shortfall. Also confirm on the real inverter that a Flux 1 slot below the current SOC stops discharge.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built

- `planning.peak_cover_plan` gains `decision="hold"`: SOC at or above `required_soc`, projected 16:00 SOC short by >= 3 points, and peak > day rate. `target_soc = required_soc`, `push_now = True`, `charge_minutes = 0`. `gain_gbp` is the peak cost avoided (kWh x (peak - day)); `gain_vs_topup_gbp` is the charge loss plus wear a later recharge would have cost.
- `PEAK_COVER_HOLD_FROM_MINUTES` (09:00): `async_run_peak_cover` now runs from 09:00, but before 12:00 it acts only on a hold; top-ups still start at 12:00. The same Flux 1 now->16:00 slot and the 16:00 restore are reused, and the score card counts holds with top-ups.
- Not verified on a real inverter: confirm that a Flux 1 slot with a target below the current SOC stops discharge before enabling `peak_cover_live`. The forecast is already inside the projection, so a sunny afternoon does not trigger a hold.
- Tests: `tests/test_peak_cover.py`.
