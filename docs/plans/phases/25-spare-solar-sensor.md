# Phase 25 — Spare solar sensor and appliance prompts

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 2, 06/10/2026 (item 25).

## Goal

Move flexible loads (dishwasher, washing machine, EV) onto free midday solar instead of exporting it cheaply and buying back later.

## Design

- Pure `planning.expected_spare_solar(hourly_forecast_kwh, correction, load_kw, battery_headroom_kwh)` → per-hour spare kWh after house load and battery refill, plus the best window (start/end/kWh).
- Sensor `Spare solar today` (kWh, attributes: best window, per-hour list); event-driven refresh at 06:00 and on forecast change.
- Optional morning notification (option, default off) when spare ≥ threshold kWh: "🔋 Sunsynk: spare solar 11:00–14:00 (~6 kWh) — good time for the washing".
- Dashboard card row via `dashboard_installer.py`.

## Acceptance

- Tests for spare-solar maths (cloudy day = 0, full battery, empty battery).
- No inverter writes.
