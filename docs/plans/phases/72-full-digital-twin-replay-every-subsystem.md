# Phase 72 — Full digital twin: replay every subsystem on half-hourly history

**Status:** Planned, not built. **Size:** XL. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Build a simulator that drives the real `planning.py` functions (target SOC, trim, export disable, peak cover, peak export, free event, gentle charge) through a half-hourly physics model of the battery. It runs on 13 months of JSONL, supports parameter sweeps and tariff swaps, and reports £ per change.

## Why (evidence)

The current backtest replays only `import_plan` and `morning_state` with two knobs. It cannot touch export, trims, peak cover or free events. Seven shadow features (15, 17, 20, 23, 30, 34, 39) wait on go-live reviews with no way to test them against each other. The hand-set thresholds in engine weakness 5 (7 kWh cliff, 85/82 trim, 35/20 nudge, 2.5 h offset) have never been tuned on data.

## Design

`tools/twin.py`: an SOC state machine with charge and discharge efficiency and a temperature derating curve. Inputs are `half_hourly_import/export`, `meter_snapshot`s and `day_actuals` solar spread over the day with `synthetic_hourly_profile`. It calls the planning functions unchanged and supports a `--grid param=a,b,c` sweep and `--tariff` overrides. Output is a CSV plus a ranked summary. Later, add Bayesian optimisation over the thresholds and post the top-3 suggestions to `#sunsynkdebug` monthly.

## Value

It turns each shadow go-live into a measured decision. If tuning removes 20–30% of avoidable import, that is about £100–150 a year. It also saves review sessions, which currently take tens of thousands of tokens each.

## Risk

Without high-resolution SOC logs the model can disagree with reality, so it must be calibrated against `morning_state` and the 22:00 SOC first.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
