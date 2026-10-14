# Phase 33 — Tariff price-change alert

**Status:** Done, merged (PR #59). **Size:** XS, ~15k actual. Runs at 06:10 (after the 06:00/06:05 jobs). **Origin:** improvement list 3, 14/10/2026 (item 3).

## Goal

Notice when Octopus changes the Flux prices (each price-cap period) so the configured `charges` don't go stale.

## Design

- Daily at 06:00: compare each band from the Octopus rate entities (phase 18, `_tariff_prices` sources) with the configured `charges` price for the same band.
- Pure `planning.tariff_price_changes(charges_prices, octopus_prices, tolerance=0.05)` → list of (band, configured, octopus).
- Notify once per change set (persist a hash): "Flux prices changed: day 23.28p → 24.10p … update Configure → tariff rows". Also feeds the tariff comparison's Flux bands (warn in that report while stale).
- No Octopus rate entities configured → no-op.

## Acceptance

- Tests: no change, small rounding within tolerance, changed band, missing Octopus band → ignored, never "0p".
