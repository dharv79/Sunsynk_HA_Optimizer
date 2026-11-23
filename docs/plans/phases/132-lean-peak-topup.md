# Phase 132: Lean peak-cover top-ups, generous holds

Status: Planned, not built

## Why

23/11/2026 the 12:27 check held 96% for the 16:00-19:00 peak: one heavy 7.1 kWh evening (22/11) lifted the p75 recent high to ~5.9 kWh, then the cold margin and discharge losses stacked on top. The user questioned holding that high when the rate after 19:00 equals the day rate before 16:00.

## Decisions

User decision (23/11/2026), after an interview:

- **Holds stay generous.** Held charge that the peak does not use is spent after 19:00 instead of grid at the same day rate, so over-holding costs ~0p/kWh while under-holding costs peak − day (~11p/kWh). The phase 123 recent high and cold margin stay for holds.
- **Top-ups go lean.** A top-up buys charge through the ~8% charge loss (plus wear), so it targets the typical evening only: the median of the last 7 logged 16:00-19:00 windows (any day type), no 10% margin, no cold margin, plus a fixed 1.5 kWh anomaly buffer (cooking, guests), then discharge losses.
- Anything short of an `ok` or `hold` on the cautious estimate is re-planned on the lean one; that re-plan may hold at the lean requirement (SOC between the two) or top up to it.
- The Peak minimum SOC still floors both. Peak export is unchanged; its floor is today's peak-cover `required_soc`, which follows the basis used.

## What

- `planning.peak_need_soc(..., buffer_kwh)` and `peak_cover_plan(..., buffer_kwh)`.
- `planning.peak_load_typical(samples, default_kwh)` — median of the last `PEAK_COVER_RECENT_DAYS` windows, default with fewer than `PEAK_COVER_MIN_PEAK_DAYS`.
- `planning.peak_cover_plan_split(...)` with `PEAK_COVER_TOPUP_BUFFER_KWH = 1.5`; adds `required_soc_hold` and `required_basis` (`hold` / `typical`).
- `opt_daytime.async_run_peak_cover` and the phase 56 intraday what-if use the split plan; the `peak_cover` record adds `peak_load_kwh_typical`, `peak_load_source_typical`, `peak_buffer_kwh`.

## Verification

`tests/test_peak_cover_split.py`; `pytest`, `py_compile` and `pyflakes` green in CI. Live: on a top-up day the `peak_cover` line shows `required_basis: typical` and a `required_soc` below `required_soc_hold`; on a hold day `required_basis: hold`.
