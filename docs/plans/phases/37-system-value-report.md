# Phase 37 — Value-of-the-system report

**Status:** Done, merged (PR #65). **Size:** S, ~30k actual. **Origin:** improvement list 3, 14/10/2026 (item 7).

## Goal

Show monthly what the battery + optimiser earn versus no battery.

## Design

- Baseline per day from meter snapshots: house load priced at the band import price for each Flux band, solar exported at the day export rate; actual = logged `daily_cost` net.
- Pure `planning.no_battery_baseline(snapshots, prices)`; days with missing snapshots → excluded, never 0.
- Monthly line in the first-Sunday report and the weekly digest: "Battery saved ~£X this month (N days)". Wear cost (phase 24) subtracted when configured.

## Acceptance

- Tests: baseline maths per band, missing snapshot excluded, wear subtraction, no cost data → `None`.

## As built

Actual is metered from the same snapshots (00:00–22:00), not the Octopus `daily_cost`, so both sides cover the same hours at the same prices. The weekly line is the debug-stream JSON (`week_battery_saving_gbp`); the monthly line is a separate first-Sunday notification.
