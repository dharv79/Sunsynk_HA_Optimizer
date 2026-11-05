# Phase 76 — Dynamic-tariff native mode (Agile / half-hourly prices in the loop)

**Status:** Done, shadow only (see As built). **Size:** XL. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Let the optimiser run directly on half-hourly import and export prices (Agile or any Octopus rate sensor), not fixed Flux bands. Charge and discharge slots come from the price curve each day, so the monthly tariff comparison becomes something the user can actually switch to.

## Why (evidence)

Phases 28 and 35 already compare Flux, Go, Cosy and Agile monthly, but control is hard-coded to Flux edges (`band_key_at`, `PEAK_START_MINUTES`, 02:00/05:00/16:00/19:00). The evidence shows Flux peak barely used (4.4 kWh peak import in a week, and `peak_export` `no_surplus` 7 of 7 days), so Flux's peak premium earns little in winter. Agile could beat it, but the system cannot run it.

## Design

Generalise the tariff layer to a 48-slot price vector from `tariff_prices_pence` or the Octopus rate entity attributes (already parsed). Add `planning.cheapest_window_plan(prices, need_kwh, rate_kw)` with tests. Requires native TOU (multi-window) or MPC to express several cheap slots. A `tariff_mode` of `flux` or `dynamic` defaults to flux.

## Value

If Agile beats Flux by 10–20% on £20/week of import in winter, that is about £100–200 a year. The digest already estimates the counterfactual.

## Risk

Depends on the unverified Agile fetch and on multi-slot control. It is a big jump from the tested Flux path. Agile price spikes need guard rails.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (05/11/2026)

- Feasibility: no locked decision or do-not-break rule touched. Nothing is pushed, so rule 16 holds trivially, and the logic is in `planning.py` (rule 19). The Agile fetch reuses the phase 35 public-API client, which needs no key.
- `cheapest_window_plan` (cheapest-first, spike guard, negative slots always taken), plus `mpc_slots(import_vectors, export_flat_p)` and `dynamic_tariff_summary`.
- 22:05 `async_run_dynamic_tariff_shadow` → `dynamic_tariff` record + `state.dynamic_tariff` + `sensor.dynamic_tariff_shadow`.
- Not built: a `tariff_mode` option that runs control on Agile. It needs multi-window control (phase 73 slots or a live MPC) and a few weeks of shadow `saving_gbp`.
