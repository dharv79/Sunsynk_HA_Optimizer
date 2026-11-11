# Phase 101 — Cloud API reliability log

**Status:** Planned, not built. **Size:** S, ~20-35k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Measure how often the Sunsynk cloud fails or is slow. Today only the last result is kept. This is also the evidence for whether phase 74 (local Modbus, XL) is worth building.

## Design

- Count per day in `api.py`: calls, failures by kind (timeout, 401, 5xx, other), re-logins, median and worst latency, pushes that needed a retry.
- An `api_health` line in the 22:00 bundle, a weekly digest line, and a `sensor.api_success_rate`.
- Alert (one Repairs issue) when failures exceed a threshold for three days running.
- No change to any push logic.

## Saving

None direct. Decides phase 74 on data.
