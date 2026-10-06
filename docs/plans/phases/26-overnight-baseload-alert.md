# Phase 26 — Overnight baseload alert

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 2, 06/10/2026 (item 27).

## Goal

Spot nights where something is left on (heater, stuck pump) — pure extra purchase.

## Design

- Capture 00:00–05:00 house load (kWh) from the load-total sensor at 05:00; log `{"type":"overnight_load"}` (dedup per day). Missing meter → `None`, never 0 (same guard as phase 12).
- Pure `planning.baseload_anomaly(history, tonight, min_nights=7, factor=1.5, min_extra_kwh=1.0)` using a home-only median (away nights excluded, do-not-break 18).
- On anomaly: notify "🔋 Sunsynk: overnight load 4.1 kWh vs usual 2.2 kWh — something left on?". Option to disable.

## Acceptance

- Tests: not enough history → no alert, normal night, anomaly, away nights excluded, `None` ignored.
