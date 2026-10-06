# Phase 24 — Battery wear cost in decisions

**Status:** Planned, not built. **Size:** S, ~20-40k est. **Origin:** improvement list 2, 06/10/2026 (item 24). Build before phase 17.

## Goal

Only cycle the battery for arbitrage when the price spread beats round-trip losses plus wear.

## Design

- Options: battery cost (£) and rated lifetime throughput (kWh); blank → wear cost `None` → decisions behave as today (convention: optional degrade gracefully).
- Pure `planning.wear_cost_pence_per_kwh(cost_gbp, lifetime_kwh)` and `planning.arbitrage_worth_it(buy_p, sell_p, round_trip_eff, wear_p)` → bool / `None` when any price missing (do-not-break 11/14 style: never treat missing as 0).
- Used by phase 17 (peak surplus export) and phase 21 (Saving Sessions); logged in the 22:00 bundle as `wear_cost_p`.
- Round-trip efficiency from phase 14 KPIs when available, else a constant default.

## Acceptance

- Tests: wear maths, worth-it true/false at the margin, `None` on missing inputs.
