# Phase 43 — Inverter fault and event log feed (feasibility-gated)

**Status:** On hold (probe built, awaiting live response). **Size:** S. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Read the Sunsynk cloud's inverter alarm and event list once a day, and also when the 02:20 watchdog or the drift check fires. Quote any new faults in that notification, log them as `inverter_event` records and expose an `inverter_fault` binary sensor.

## Why (evidence)

Open item: nobody has found out why the 12/10 overnight charge stopped at 83% at 04:25 (target 100), and the note says to 'check the Sunsynk Event log' by hand. This week the morning SOC fell short of 100 on 6 nights (98/95/96/81/94/95). api.py calls only the income and settings endpoints (lines 156–180), so no alarm data is read.

## Design

First probe the `api/v1/` alarm and event routes with the existing token and record the response shape in the phase file. Go On hold if no route exists, as phases 23 and 28 allowed. If one does, add `SunsynkApi.async_read_events(sn, since)` with the existing 401 re-login, and `planning.classify_inverter_events(events, last_seen_id)` with tests (charge-blocking, BMS, grid, info). Call it from `async_run_charge_watchdog_recheck` (optimizer.py:1771), `async_run_drift_check` (:3284) and a daily job. Log `inverter_event` records with dedup on event id. Add the binary sensor.

## Value

Turns hours of manual diagnosis into one line in the alert. Each missed full charge costs about 1–2 kWh of day-rate import (about £0.10–0.50), and repeat failures add up.

## Risk

The endpoint is undocumented, and the codes may be numeric with no text. Sized S assuming the probe succeeds.

## Vetting note

Two candidates merged (alarm reader and event log feed). Confirmed that api.py has no alarm or event call. It directly closes a named open item, and the feasibility gate keeps the risk low.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (04/11/2026)

Feasibility step only: service `probe_inverter_events` GETs six candidate alarm/event routes and returns status plus a truncated body (and posts it to the debug stream). Call it from Developer tools, record the working route and shape here, then build `async_read_events`, `classify_inverter_events`, the `inverter_event` record and the binary sensor. On hold until then.

## Probe round 1 result (04/11/2026)

- `/api/v1/inverter/{sn}/alarms|events|event/list`: HTTP 200 but `{"code":2,"msg":"No Permissions"}` (route exists, token not allowed).
- `/api/v1/plant/{plant_id}/alarms` and `/api/v1/event/inverter/{sn}`: 404.
- `/api/v1/plant/{plant_id}/events`: exists; 400 `plantEventsDTO.lan must be not null`. So this is the viable route and needs `lan` (language, e.g. `en`).
- Round 2 (b38): probe now sends `lan=en` plus type/sn/status variants and returns up to 1500 chars of body. Awaiting that response to fix the shape.

## Probe round 2 result (04/11/2026)

All six `plant/{id}/events?...&lan=en` variants (type, sn, status) return `{"code":500,"msg":"Internal Server Error"}`: `lan` is accepted but another required parameter is missing. Round 3 (b39) adds a 30-day date range (`startTime/endTime`, `startDate/endDate`, `beginTime`, `date`) and `plantId`. If this also fails, shelve phase 43 as no readable endpoint (as phases 23 and 28 did) and keep the manual Sunsynk Event log check.
