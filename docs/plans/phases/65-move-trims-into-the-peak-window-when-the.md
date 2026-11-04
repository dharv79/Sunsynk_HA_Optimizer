# Phase 65 — Move trims into the peak window when the solar left today can wait

**Status:** Done, merged (634ea94, PR #117), shadow. **Size:** M. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Before 16:00, trim only the kWh that would otherwise overflow (remaining forecast PV minus load minus headroom). Hold the rest and sell it 16:00-19:00 at the peak export rate through the peak-export path.

## Why (evidence)

Weakness 8. Trims (SOC > 85 to 82, up to about 28 pushes a sunny day) sell at the day export rate with no `arbitrage_worth_it` check. Flux peak export pays much more than day export. The prior week's peak-export shadow showed 8.1 kWh and £0.67 available. `trims_off` is shadow only.

## Design

Add `planning.trim_defer_kwh(soc, cap, remaining_pv_kwh, load_kw, peak_export_target_soc)`, which returns the trim size that keeps exactly enough headroom for the remaining PV. Call it from `trim_target_soc` callers in `async_run_flux2_check`. Ship behind `trim_defer_live` (default off), logging the deferred kWh and price gap in a `trim_shadow` record. It depends on `peak_export_live` for the sale.

## Value

About 2-4 kWh a sunny day moved from day export to peak export (a gap of about 12-15p/kWh), roughly £0.30-0.50 a day over about 120 days, so £35-60 a year. Also about 10-20 fewer trim API pushes a sunny day.

## Risk

If PV beats the forecast, it is clipped at the export limit. Use `clipped_solar_kwh` as the stop-loss signal and include the intraday ratio.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (04/11/2026)

`planning.trim_defer_kwh` splits a trim into the overflow to trim now and the kWh that can wait for the peak (PV to 16:00 minus load, beyond headroom; no forecast or clipped solar today defers nothing; deferrals under 0.3 kWh ignored). Every pre-16:00 trim logs a `trim_shadow` record. `trim_defer_live` (default off, also needs `peak_export_live`) trims to the smaller target or skips the push (`trim_deferred`). Solar uses the higher of raw and intraday-scaled so a surprise sunny day still trims. Not built: a backtest of the logged records.
