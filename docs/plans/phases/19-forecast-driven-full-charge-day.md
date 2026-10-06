# Phase 19 — Pick the full-charge day from the solar forecast

**Status:** Done, merged (449434d, PR #32). **Size:** S, ~45k actual.

**As built:** Forecast.Solar only gives today/tomorrow, so (user choice) the Sunday weather pick stays and an 18:00 daily re-check moves the full-charge day to tomorrow, at most once a week, when tomorrow's corrected kWh can fill the battery plus daytime load and its weather beats the chosen day. Logic: `planning.full_charge_day_move`; details in `docs/architecture/import-plan.md`. **Origin:** improvement list 06/10/2026 (item 8).

## Goal

The Sunday 18:00 selector scores weather text with weekday penalties. Prefer the day with the most forecast solar kWh so 100% comes from PV, not grid.

## Design

- Optional config: multi-day solar forecast source (e.g. Solcast day-N sensors or a forecast attribute list). Check what the user's forecast integration exposes before building.
- Pure scorer: forecast kWh primary, existing weather score as tie-break; keep weekday penalties optional.
- Fallback to the current scorer when no multi-day forecast is configured.

## Acceptance

- Tests: highest kWh wins, tie-break, fallback path identical to today.
