# Phase 67 — Cache config and tariff parsing on the SOC-change path

**Status:** Done (04/11/2026). Design and verification: `docs/architecture/code-map.md` (SOC-path caching). Vetted: no do-not-break rule affected; the cache is keyed on entry data/options identity, not a per-slot key, and the price cache is keyed on the rates entity's `last_updated`. **Size:** S. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Cache merge_entry_data once per entry load, and cache _tariff_prices per date and half-hour slot. Skip update_state(touch=True) when nothing has changed, so each SOC tick stops doing about 25 deep copies, about 400 ISO date parses and 2 recorder writes.

## Why (evidence)

The optimizer.cfg property (optimizer.py:361) deep-copies the merged config on every access, and the ~60 *_entity properties go through it, which is about 20-25 copies per SOC event. _async_refresh_marginal_cost → _tariff_prices re-parses 4 bands × ~100 Octopus slots with two ISO parses each. Above 85% SOC, every change also runs async_run_flux2_check, whose touch=True changes updated_at, so sensor.last_updated and the flux2_action attributes write recorder rows. Options changes already reload the entry, so a cache is safe.

## Design

In SunsynkOptimizer.__init__, set self._cfg = merge_entry_data(...) and have the cfg property return a read-only MappingProxyType. Add self._price_cache keyed by (date, minute//30) and cleared at the 06:10 tariff check. In coordinator.update_state, skip the save and the listener notify when the diff is empty, and only touch when something changed. Extend test_code_integrity with an AST check that nothing mutates self.cfg.

## Value

Roughly 95% less CPU on the SOC listener. It removes ~100-300 recorder writes a day on sunny days (smaller HA database, less SD-card wear on a Pi). No effect on money, but lower latency for the trim and export decisions.

## Risk

Code that mutates the dict returned by self.cfg would break. The read-only proxy surfaces that straight away in tests.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
