# Phase 60 — Peak cover sizing: add wear and stop stacking p75 with a 1.1 margin

**Status:** Built (shadow-safe: peak cover is already off by default via `peak_cover_live`). **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Make peak_cover_worth_it include the phase 24 wear cost. Size the peak load from a weekday-split median of peak_window_usage instead of p75 × 1.1, so top-ups are not over-bought.

## Why (evidence)

On 31 Oct the top-up assumed 4.0 kWh of peak load, but 2.4 kWh was used, so 1.6 kWh was over-bought and the battery ended at 29% instead of the 25% floor. peak_cover_worth_it (planning.py:1053) is just peak > day/0.92, which is 38.88 > 30.18. That leaves only 8.7p/kWh of margin, and the wear term used by arbitrage_worth_it and peak_export_plan (planning.py:937, 984) is ignored, so with 6-9p wear the claimed £0.41 gain is close to zero or negative. On top of that, peak_need_soc applies PEAK_COVER_MARGIN=1.1 to a p75 estimate (peak_load_estimate_kwh), which is two layers of pessimism.

## Design

planning.peak_cover_worth_it(day, peak, efficiency, wear_pence) returns peak > day/eff + wear. In planning.peak_load_estimate_kwh, use the median split by weekday/weekend from the async_recent_peak_loads history, and drop the margin to 1.0 when ≥7 days exist. Pass self._wear_pence() in optimizer.async_run_peak_cover. Log gain_after_wear_gbp. Add tests next to the existing peak_cover tests.

## Value

Avoids ~1.5 kWh/event of unneeded day-rate charging, plus top-ups that lose money once wear is counted. Roughly £0.10-0.40 per top-up day, ~£10-20/yr. It also stops about 50-100 kWh/yr of needless cycling.

## Risk

A median under-covers the occasional heavy evening, which then imports at 38.88p. Mitigate by keeping p75 on days with a known high load (e.g. weekends) and logging shortfalls.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## Built

- `planning.peak_cover_worth_it(..., wear_pence)`: `peak > day/eff + wear`; unknown wear counts as none. `peak_cover_plan(..., wear_pence)` uses it and adds `gain_after_wear_gbp` (the old `gain_gbp` is kept, before wear).
- `planning.peak_load_estimate_split(samples, default, weekend)` -> `(kWh, source, margin)`. Weekdays with >= 7 weekday samples use the median and margin 1.0 (`history_weekday_median`). Weekends and thinner history keep the all-days p75 and `PEAK_COVER_MARGIN` 1.1.
- `DataLogger.async_recent_peak_load_days` supplies (date, kWh). `async_run_peak_cover` passes the margin and `_wear_pence()` and logs `peak_margin`, `wear_cost_p`, `gain_after_wear_gbp`.
- The 01:55/16:00 peak-export sizing (`peak_need_soc`) is unchanged and still uses p75 x 1.1.
- Tests: `tests/test_peak_cover.py`.
