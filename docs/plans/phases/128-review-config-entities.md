# Phase 128: Review fixes: config, entities, diagnostics and cards

Status: Done, merged (b645f43, PR #248)

## Why

Fix the configuration, entity, diagnostics and dashboard-card findings of the 21/11/2026 code review. Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

Out of scope (user decision 21/11/2026): a reauth/reconfigure step for the Sunsynk login.

## What

- merge_entry_data shares the options' nested lists, so an options-flow edit to only the tariff rows is never saved or reloaded (`flux_helpers.py:228`, medium)
- Optional text options declared with default= cannot be cleared, so 'blank = off' never takes effect (`config_flow.py:543`, medium)
- Heat map card defines its element immediately, bringing back the 'Configuration error' load race fixed in phase 80 (`sunsynk-optimizer-heatmap.js:209`, medium)
- Diagnostics download does not mask the notify service, notify target or data report target (`redact.py:77`, low)
- Three redaction lists diverge; settings snapshot/audit bypass the dump's identifier masking (`settings_audit.py:36`, low, plausible)
- In monitor mode the adaptive sensors report made-up defaults (1.0, 0%, 0%, 0 days) that go into long-term statistics (`sensor.py:330`, low)
- Initial-setup errors invalid_time, quiet_pair and required have no translation (`strings.json:82`, low)
- Day chart puts readings an hour out on clock-change days (`sunsynk-optimizer-day-chart.js:11`, low)
- Heat map stops redrawing on resize after its view is detached and re-attached (`sunsynk-optimizer-heatmap.js:81`, low)

## Verification

New or updated HA-free tests for each fixed item; `pytest`, `py_compile` and `pyflakes` green in CI.
