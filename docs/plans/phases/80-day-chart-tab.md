# Phase 80 — Day chart tab with previous/next day navigation

**Status:** Done, merged (93af8a5, PR #159). **Size:** M. **Category:** user request (05/11/2026, out of the idea sweep).

## Goal

A chart like the Sunsynk app's Generation chart (PV, battery, load, grid in W; SOC in %) for one day at a time, with navigation back through previous days. Decisions (user, 05/11/2026): its own "Day chart" tab; minute detail while the recorder has it, hourly averages after that.

## Design

- `www/sunsynk-optimizer-day-chart.js`: dependency-free custom element `custom:sunsynk-optimizer-day-chart`, served with the flow card by `card_resource`. SVG chart drawn at the card's real width; 5-minute bins; tap/hover cursor; legend toggles; ◀ / ▶ / date picker / Today; day totals from the daily counters.
- Data: recorder history for the day; if the day has no detail, hourly long-term statistics (noted on the chart); otherwise "no data". Fetched per day, today refreshed every 5 minutes.
- Dashboard: "Day chart" panel view after Overview (`dashboard_installer._day_chart`).
- Fix in the same change: both bundled cards define their element only after `<home-assistant>` is defined, so the frontend's scoped registry sees them. This was the cause of the intermittent "Configuration error" on the flow card after 1.0.12b48.

## Acceptance

- Tests: tab order and entities, card file reads every config key and uses both data sources, `card_resource` serves every bundled card.
- Real-HA check (HA 2025.10.1): detail day, hourly-statistics day, no-data day, today; 1440 and 390 px; 10/10 fresh loads define both cards.
- README, architecture doc and plan index updated; py_compile, pyflakes, pytest green.
