# Phase 74 — Local Modbus/RS485 control path as a cloud-outage fallback

**Status:** Planned, not built. **Size:** XL. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Add an optional local control path (an RS485-to-WiFi dongle, or Solarman/ESPHome Modbus) that mirrors the cloud writes. When the Sunsynk cloud is slow or down at 01:55, the plan still lands, and reads become real-time.

## Why (evidence)

Reliability evidence: the 29 Oct 01:55 plan record is missing entirely. `_authorized_json` sets no timeout, so the default is 5 minutes and could stall into the 02:00 window. The plan retry and charge watchdog (phases 22 and 32) exist because pushes fail. The Flux read-back is undocumented. The 12/10 charge stopped at 83%, with the cause unknown.

## Design

New `local_transport.py` with the same interface as the relevant `api.py` methods, backed by pymodbus TCP against an RS485 gateway (new hardware, about £20–40). A config option picks cloud, local, or cloud-then-local fallback. Map registers for grid charge current and the TOU slots. Log a `transport_used` field on pushes. Monitor mode still blocks writes on both paths.

## Value

Avoids missed overnight charges. Each one costs about £1.50–3.00 (10 kWh at the day rate instead of off-peak), so preventing 3–5 a year is worth about £5–15, plus second-level telemetry for every other model.

## Risk

Needs new hardware. Register maps differ by firmware. Concurrent cloud and local writes could conflict, so one path must clearly own control.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
