# Phase 69 — Receding-horizon MPC planner (48 h, half-hourly) in shadow

**Status:** Planned, not built. **Size:** XL. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Replace the rule tree (target SOC, trim, peak cover, peak export, Flux 1 sizing) with one optimiser. Every 30 min it solves a small linear program over the next 48 half-hours: tariff, forecast solar, learned load, battery limits, losses and wear. It outputs the slot plan, and the existing push path applies it.

## Why (evidence)

The rules contradict each other in the 25–31 Oct evidence. On 27 Oct it charged to 95% from 9.9 kWh of off-peak import on a 5.91 kWh forecast, then exported 8.7 kWh when solar came in at 15.2. On 30–31 Oct the battery ended at the 25–29% floor with 5.6–6.2 kWh of day import at 27.77p. On 31 Oct peak cover bought 4.71 kWh for a load that turned out to be 2.4 kWh, not the 4.0 assumed. Avoidable import was £4.99 that week and £10.37 the week before. Engine weaknesses 1, 7, 8 and 10 (double-counted drain, fixed timing, three corrections pulling apart) all come from separate rules with no shared objective.

## Design

New `planning_mpc.py`, HA-free and pure Python. Use a dynamic-programming search over SOC in 1% steps × 96 slots so no solver dependency is needed. Inputs reuse `tariff_prices_pence`, `learned_load_kw` (or idea 3's profile), `synthetic_hourly_profile`, `charge_efficiency`, `DISCHARGE_EFFICIENCY` and the battery wear cost (phase 24). Run it every 30 min from the existing tick and log an `mpc_plan` record next to the rule-based decision. Add `tools/backtest.py --planner mpc` to compare the two on logged nights. A `mpc_live` option (default off) maps the first slots onto the 2 Flux slots through `_async_post_with_status`.

## Value

If it removes half of the logged avoidable import: about £250–500 a year (£5–10 a week). It also cuts trim pushes, which can reach about 28 a day.

## Risk

The Flux income API exposes only 2 slots, so the plan has to be simplified to fit them (see the native TOU idea). The optimiser is only as good as the load and forecast inputs. It must stay shadow-only until the backtest agrees.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
