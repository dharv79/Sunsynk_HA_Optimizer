# Phase 121 — Find and stop the daily API "other" failures

**Status:** Done, merged (46a6a5b, PR #239), released in 2.3.4. **Size:** XS-S, ~15-25k. **Origin:** user request, 19/11/2026 (debug stream review).

## Goal

Name the cloud call behind the daily "other" API failures, and stop the failing call if it is the optional Flux read-back.

## Why (evidence)

`api_health` lines 13-18/11/2026: about 1 call in 6 fails every day, always "other" (no timeout, 401 or 5xx, no re-logins or push retries). 18/11: 15 of 82; 14/11: 15 of 80; 15/11: 10 of 61. Plans were pushed every night. The rate is steady regardless of volume, which fits one call failing on every drift check: each check makes 5 calls (events, counts, battery health, notices, Flux read-back), and one in five is about 20%. The four reads come from portal captures; the Flux read-back (`GET /api/v1/plant/{id}/income`) is undocumented, and its errors were logged at debug and retried every 2 h. The daily health line has read "Check" every day for this reason alone.

## Design

- `planning.api_route(url)`: `/api/v1/plant/{id}/income` style label; any path segment holding a digit becomes `{id}`, the query is dropped, so no identifier is logged.
- `planning.failure_detail(exc)`: class, HTTP status and the message with digit runs of 4+, emails and URLs masked, 120 chars.
- `ApiStats.record(kind, latency, route, detail)`: failures counted per `"<METHOD> <route>"`, first detail kept per route. The 22:00 `api_health` line gains `failures_by_route` and `failure_detail`.
- Flux read-back: after `READ_BACK_FAIL_LIMIT` (2) consecutive failures (`planning.read_back_should_stop`) it switches off until restart with one warning, like the existing "no fluxProducts" path. The grid-current half of the drift check is unchanged.

## Acceptance

- Pure logic in `planning/api_health.py` with tests in `tests/test_api_health.py`.
- After release, the 22:00 `api_health` line names the failing route. If it is the income GET, failures stop after the second check of the day and the health status returns to OK. If it is another route, the detail says why and a follow-up fixes that call.
- py_compile, pyflakes and pytest pass.
