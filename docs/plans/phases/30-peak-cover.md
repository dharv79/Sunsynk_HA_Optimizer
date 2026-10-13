# Phase 30 — Peak cover

**Status:** Done, merged (PR pending). **Size:** S–M, ~45k actual. **Origin:** user request 13/10/2026 after the 12/10 low-solar day (debug data + Sunsynk app chart).

## Goal

Make sure the battery reaches 16:00 with enough charge to run the house through the 16:00–19:00 peak window, topping up at the cheaper day rate (27.77p) instead of buying at the peak rate (38.88p).

## Evidence

12/10: 83% at 04:25 (charge stopped there, target 100) → drained to the 25% low-battery limit by ~14:30 on a dull day (3.3 kWh solar, 17.7 kWh load) → grid charge 15:00–17:30, part of it inside the peak (2.7 kWh bought at 38.88p). Other days peak imports were 0.1–0.4 kWh.

## Design

- `planning.peak_cover_plan` (+ `solar_until_kwh`, `daytime_load_kw`, `peak_load_estimate_kwh`, `peak_cover_worth_it`): projected SOC at 16:00 from current SOC, raw (pessimistic) solar forecast to 16:00 and house load since 05:00 (meter snapshots; fallback = plan load), against the SOC needed = 25% floor + 75th-percentile recent `peak_load_kwh` × 1.1. A battery that would run flat sits at the floor.
- Check every 30 minutes 12:00–16:00. `top_up` only when charging at the day rate beats the peak rate after charge losses (`peak_cover_worth_it`; missing price → `no_price`, never 0p). The push starts once the charge would need to begin (+45 min lead) so later solar can still help.
- Live (`peak_cover_live`, default off): Flux 1 slot now→16:00 with `targetSoc` = required SOC. The inverter only imports while below the target, so it holds the battery there at the day rate. At 16:00 the normal plan is restored (`_async_peak_cover_end`, from the peak-export callback).
- Shadow: one `peak_cover` record per day (first actionable top-up, else the last check before 16:00) in the 22:00 bundle; dedup per date.
- Never pushes in monitor mode or while a free event / saving session holds the slots.

## Acceptance

- Tests: shortfall/ok, timing, solar closing the gap, full cap, missing prices, not-worth-it, solar/load/peak-load helpers (`tests/test_peak_cover.py`).

## Outcome (13/10/2026)

Built as designed. Not covered: why the overnight charge stopped at ~83% on 12/10 (04:25, target 100) — check the Sunsynk Event log; separate from this phase. Details: `docs/architecture/export-control.md`.
