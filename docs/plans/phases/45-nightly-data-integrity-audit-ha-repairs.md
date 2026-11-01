# Phase 45 — Nightly data-integrity audit, HA Repairs and a diagnostics download

**Status:** Planned, not built. **Size:** M. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

A morning self-audit of the last 14 days of JSONL. It finds missing, duplicated or self-contradictory records, raises and auto-clears HA Repairs issues, re-tries fill-only backfills, and adds `diagnostics.py` so a redacted debug bundle can be downloaded from the HA UI.

## Why (evidence)

Problems from one week of `#sunsynkdebug` were all found by hand: the 29 Oct `import_plan` was missing, the 25 Oct `daily_cost` was never posted, `battery_cycled_kwh` was 0.0 and `unused_charge_kwh` null on 29–30 Oct despite large SOC swings, and self-sufficiency read 0.0% on 25 Oct with 13.7 kWh of solar. The repo has no `issue_registry` or diagnostics code.

## Design

Add `data_logger.audit_days(records, days=14)` as a pure function with tests, returning issue dicts (missing type per date, duplicate, contradiction rules such as cycled=0 with an SOC swing over 30 points). Run it as an optimizer job around 06:05 under `_guarded`. Call `ir.async_create_issue` once per issue class and delete the issue once it clears. Re-run `_read_octopus_previous_day_cost` for missing settled dates (fill-only, rule 12). Add a `sensor.data_health` gap count, and a `diagnostics.py` that redacts credentials and dumps state, the last 7 paired days and the merged config.

## Value

Keeps the learned corrections honest. A bad input moved the drain adjustment by 20 points, about 2 kWh of target. It also saves about 30 min/week of reading Slack and gives a one-click debug bundle.

## Risk

Rules need tuning so Repairs does not become noise. The audit must never rewrite records, only fill gaps under the existing rules.

## Vetting note

Grep found no issue_registry or diagnostics module. The evidence is concrete. It respects rules 12 and 13 by being fill-only. It is a new capability rather than a tweak to an existing one.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
