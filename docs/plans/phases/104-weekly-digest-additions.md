# Phase 104 — Weekly digest: import causes, charge shortfalls and shadow results

**Status:** Built, merged (c0279b6, PR #211). **Size:** S, ~25-40k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building. Finishes the parts of phases 42 and 47 that were not built.

## Goal

The Sunday digest reports cost and KPIs but not why. Add the explanation that is already being logged.

## Design

- "Why did I import" breakdown for the week from the phase 42 ledger (kWh and £ by cause).
- Charge outcome: nights that missed the target, by how much, and the likely cause (the cold nights of 06-10/11/2026 reached 87-96% against 100%).
- Forecast: the week's forecast error, raw against corrected.
- One line per shadow feature that changed verdict this week.
- Weekly KPI sensors (phase 47 leftover) so the dashboard can chart week on week.

## Saving

None. Reporting.

## Built (13/11/2026)

`planning/digest.py`, `_async_weekly_digest`, `digest` in the weekly summary and AI prompt, `sensor.kpi_week_*`. Detail in `docs/architecture/weekly-reports.md`. Deviation: no separate human message; the digest rides the existing debug-stream summary and the AI insight.
