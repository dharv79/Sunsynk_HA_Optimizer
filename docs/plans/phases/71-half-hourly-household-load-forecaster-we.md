# Phase 71 — Half-hourly household load forecaster (weather and calendar aware)

**Status:** Done, shadow by default (see As built). **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Learn a 48-slot load profile per day type from Octopus half-hourly import, the meter snapshots and SolarSynk load. Condition it on outside temperature, away mode and the calendar. All load-based decisions (evening reserve, bridge, peak cover) then read the right time band instead of one overnight kW figure.

## Why (evidence)

Engine weakness 3: `learned_load_kw` comes from 00–06 only, yet it drives the 19:00–02:00 reserve and the 16–19 peak fallback. On 31 Oct peak cover assumed a 4.0 kWh peak against 2.4 actual and bought 4.71 kWh at 27.77p. Half-hourly import is logged (`half_hourly_import`) but used only for the tariff comparison. Weekly load is 116 kWh, with off-peak 62.5, day 34.6 and morning 26.8 kWh.

## Design

`planning.load_profile_forecast(records, weather, date)`: a per-slot median with a temperature regression term (heating-degree slope), fitted from `half_hourly_import`, adding back solar self-use from `meter_snapshot` deltas. Feed it to `evening_reserve_soc`, `peak_cover_plan` and the bridge walk behind a `load_profile_live` flag, and log both values in shadow. Add a backtest mode that scores the profile against actual `peak_window_usage`.

## Value

Fewer unnecessary peak-cover top-ups (about £0.20–0.40 each) and fewer evenings ending at the floor. Roughly £50–100 a year, plus a better input for the MPC.

## Risk

Half-hourly data is import only, so it misses load met by solar or battery and must be reconstructed. Octopus data arrives days late.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (05/11/2026)

- Feasibility: no locked decision touched; the live path is behind `load_profile_live` (off), logic in `planning.py` (rule 19).
- Load is reconstructed per segment from the SolarSynk load meter (so solar- and battery-met load is included); half-hourly import only shapes a segment when it accounts for ≥90% of that segment's load.
- `day_load_slots`, `load_profile`, `heating_scale` (needs `outside_temp_c` history, logged from now), `load_profile_backtest`.
- Feeds the shadow MPC always; evening reserve and peak cover when live. The bridge walk is unchanged (it uses the overnight rate, which the profile's early slots match).
- Not built: calendar/holiday terms beyond weekday/weekend and away.
