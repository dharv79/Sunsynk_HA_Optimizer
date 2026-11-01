# Phase 75 — Battery state-of-health and degradation ledger

**Status:** Planned, not built. **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Track equivalent full cycles, throughput, average depth of discharge, temperature exposure and estimated capacity fade over months. Feed a per-cycle cost from that degradation back into the wear term, and warrant-check it against the battery's lifetime kWh.

## Why (evidence)

Phase 34 capacity tracking stays in shadow, samples only October–March 19:00–22:00, and is never fed back. The wear cost (phase 24) uses fixed `battery cost / lifetime kWh`. In the evidence, 22:00 SOC hit the 25% floor on 4 of 7 days and `battery_cycled_kwh` logged 0.0 on 29–30 Oct even though SOC swung 81→25 and 94→25, so cycle accounting is unreliable. `battery_etoday_charge/discharge` are unused.

## Design

`data_logger` gets a `battery_health` monthly record built from `battery_etoday_charge/discharge` (fixing the zero-cycled bug path), time at 100% or ≤25%, and mean temperature. `planning.degradation_cost_per_kwh(efc, dod_hist, temp_hist)` uses a simple empirical LFP fade model, with tests. It feeds `arbitrage_worth_it` behind a flag. A quarterly report says 'capacity X kWh, Y% of warranty throughput used'.

## Value

Avoids cycling for gains below the true wear cost (31 Oct: a £0.41 'gain' top-up). It could extend useful life by months on a £3–5k battery, worth about £50–150 a year amortised.

## Risk

Fade models are rough without BMS state-of-health. The Sunsynk API battery state-of-health endpoint is unverified.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
