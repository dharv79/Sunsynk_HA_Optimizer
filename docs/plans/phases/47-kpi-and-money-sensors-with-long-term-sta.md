# Phase 47 — KPI and money sensors with long-term statistics

**Status:** Planned, not built. **Size:** S. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Expose daily and weekly KPIs as proper statistics sensors: avoidable import £, battery saving £, self-sufficiency %, unused charge kWh, over-limit import kWh and peak export £. Group every entity under one DeviceInfo so HA keeps history and Energy-style graphs.

## Why (evidence)

These KPIs exist only in Slack JSON and the weekly digest, for example avoidable import £4.99/week and battery saving £0.51–1.18/day. Grep finds no `SensorStateClass` and no `device_info` anywhere in the platforms, so HA records no long-term statistics and the entities are not grouped under a device.

## Design

Add sensor entities in sensor.py, updated from the 22:00 `day_kpis` result and the Sunday `weekly_summary`. Use `SensorStateClass.MEASUREMENT` for percentages and kWh-per-day, and `TOTAL` with `last_reset` for daily £ (device_class MONETARY, GBP). Add a shared `DeviceInfo(identifiers={(DOMAIN, inverter_serial)})` to every platform (rule 2: serial, not plant_id). Add a dashboard_installer card. Values follow rule 11 and are unavailable, not 0, when cost is missing.

## Value

Puts the money on the HA dashboard without Slack, and keeps a free year of trend history in the recorder.

## Risk

Statistics metadata must be right first time, because a later unit or class change needs a statistics repair. Adding DeviceInfo regroups the existing entities, which should be harmless.

## Vetting note

Confirmed: no SensorStateClass and no device_info anywhere. Low effort and high visibility. It is a new capability (history and statistics), not a change to decision logic.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
