# Phase 35 — Fuller tariff comparison (standing charges, VAT, Agile/Intelligent)

**Status:** Done, merged (PR #60); Agile fetch unverified live. **Size:** M, ~55k actual. **Origin:** improvement list 3, 14/10/2026 (item 5; completes phase 28's deferred part).

## Goal

Make the monthly comparison match a bill: add standing charges and VAT, and add Agile and Intelligent Octopus Go.

## Design

- Options: standing charge p/day per tariff (dated defaults; Flux 54.62p from the user's bills), VAT % (default 0 from 01/10/2026 per bills; dated constant).
- Agile: historic half-hourly rates from the public Octopus API (`/v1/products/<agile>/electricity-tariffs/<region>/standard-unit-rates/`), fetched once per month, cached in the data dir; region from the MPAN/tariff code. Intelligent: fixed 23:30-05:30 cheap window (smart-charge extras out of scope).
- `planning.cost_under_tariff` gains standing charge and VAT; Agile uses per-slot prices.
- Report: totals inc. standing charge and VAT, with the unit-rate-only figure kept for continuity.

## Acceptance

- Tests: standing charge × days, VAT on/off, Agile slot pricing, API unavailable → Agile `None` (others still reported).
