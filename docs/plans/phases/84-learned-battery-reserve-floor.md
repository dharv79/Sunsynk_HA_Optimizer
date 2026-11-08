# Phase 84 — Learned battery reserve floor

**Status:** Planned, not built. **Size:** S, ~25-35k est. **Origin:** idea sweep 08/11/2026 (idea 11).

## Goal

Tighten the minimum-SOC reserve to the lowest level the house actually reaches, so the overnight charge buys less cushion than needed.

## Vetting (08/11/2026)

- `lowest_soc` is already computed in `planning.py` and phases 55, 60 and 64 size the evening reserve and the bridge, but no step learns the floor itself from `day_actuals` / `morning_state`.
- Overlaps phase 64 (newsvendor bridge target); re-read it first and merge if the same margin is already covered.

## Design

- Pure `planning.learned_reserve_floor(history, min_days=21, quantile=0.1)`: the 10th percentile of the lowest SOC reached over home days (away days excluded, do-not-break 18), clamped to the configured reserve and never below the inverter shutdown SOC.
- Shadow only first: log `reserve_floor_learned` and the kWh it would have saved per night.

## Savings estimate (rough, unproven)

Roughly £5-15/yr. Each 5 points of reserve is about 0.5 kWh a night on a 10 kWh battery, and only some of it is bought at the higher rate.
