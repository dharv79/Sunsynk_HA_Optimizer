# Phase 34 — Battery capacity tracking

**Status:** Done, merged (2c8963c, PR #66), shadow. **Size:** S, ~35k actual. **Origin:** improvement list 3, 14/10/2026 (item 4).

## Goal

Measure usable capacity over time (datasheet: Sunsynk W5.3, 4.79 kWh usable per module) so sizing uses the real figure and degradation is visible.

## Design

- Sample windows with a large SOC swing and no ambiguity: overnight charge nights (grid kWh in from meter snapshots 02:00→05:00 minus in-window load, × charge efficiency) over SOC gained; evening discharge 19:00→02:00 (load − grid import) over SOC lost.
- Pure `planning.capacity_estimate(samples)` → median kWh per 100% with ≥5 samples, else `None`.
- Log `capacity_estimate` monthly; dashboard attribute; alert when below 90% of configured `battery_capacity`.
- Shadow: never changes `battery_capacity` automatically (an option later).

## Acceptance

- Tests: clean samples, small swings excluded, missing meters → `None`, away days allowed (physical property).

## As built

The discharge sample uses 19:00→22:00, in October–March only: the meters reset at midnight, so the 22:00–24:00 hours are missing, and summer evenings have solar. The estimate is shown as a diagnostic sensor rather than an attribute.
