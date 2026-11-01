# Phase 57 — API request timeout and catch-up for missed monthly reports

**Status:** Planned, not built. **Size:** XS. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Give SunsynkApiClient._authorized_json an explicit aiohttp.ClientTimeout (about 30 s), so a hung cloud call fails fast and the phase 32 retry gets a chance before the cheap window. Persist the last-run month for the monthly reports (battery value, tariff comparison, capacity estimate) and run a missed one at the next 18:00 or at startup.

## Why (evidence)

api.py:147 calls self._session.request with no timeout, while octopus_public.py uses ClientTimeout(total=30). _async_send_monthly_battery_value and the tariff comparison return early when today.day > 7, and the capacity estimate is gated `day <= 7` (optimizer.py:1560). All of them run only from the Sunday 18:00 handler, so if HA is down that one Sunday, the month's report is lost.

## Design

Add a module _TIMEOUT in api.py and pass timeout= on both the request and the re-login. Add last_monthly_report_month to OptimizerState through coordinator.update_state. Add planning.monthly_report_due(last_month, today), called from the 18:00 dispatcher daily and from the startup refresh, and write the state only after a successful send. Tests for report_due.

## Value

No lost monthly reports, and plan pushes that fail in seconds rather than minutes. A missed overnight plan costs about £1-3.

## Risk

Duplicate reports if the state isn't written after the send. A 30 s timeout might cut a slow but successful settings read, so log timeouts distinctly.

## Vetting note

Confirmed: there is no timeout in api.py, and the monthly gating is in optimizer.py:1553-1565, 2575-2578 and 2691-2693. I folded the weekly digest out because it runs every Sunday anyway.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
