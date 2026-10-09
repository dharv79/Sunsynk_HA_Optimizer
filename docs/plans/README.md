# Plan index

## Context

Sunsynk HA Optimizer 1.0.11 is the latest stable release (Octopus cost link, weekly digest, cost-aware export in shadow mode, optional AI insight, startup re-plan labelling; promoted from 1.0.11b15); 1.0.12b3 (beta) adds phases 11–17, 19, 20 and 22 (1.0.12b2 had 11, 19 and 20). Remaining work is follow-ups surfaced by the `#sunsynkdebug` Slack stream.

## Phase status

| Phase | Status | Size / tokens |
|---|---|---|
| 1. Octopus cost link (1.0.11b1) | Done, merged (b0143e6) | M, actual not recorded |
| 2. Weekly cost digest (1.0.11b2) | Done, merged (d99129a) | S, actual not recorded |
| 3. Cost-aware export threshold, shadow mode (1.0.11b3) | Done, merged (8ffcf42) | M, actual not recorded |
| 4. AI Task weekly insight (1.0.11b4) | Done, merged (0f162a0) | S, actual not recorded |
| 5. Octopus late-settlement handling (1.0.11b5–b7) | Done, merged (763a8c0, PRs #12–#15) | S, actual not recorded |
| 6. Planning refactor + import_plan field fix (1.0.11b8) | Done, merged (889e6a8, PR #16) | L, actual not recorded |
| 7. Gas cost per-sensor dating fix (1.0.11b9) | Done, merged (2eb679c, PR #17) | S, actual not recorded |
| 8. Weekly digest covers a full 7 days | Done, merged (6f45de0, PR #19) | XS, ~15k actual |
| 9. Cost-aware export go-live decision | Done: decided to keep shadow mode (no code change) | XS, ~15k actual |
| 10. Free electricity event (sell before, refill during) | In progress — planner done (a7fb3ee, PR #23); manual entry done and merged (cbee324, PR #25; in 1.0.11b13); validation/startup-warning fixes merged (fb0d791, PR #27; in 1.0.11b14); Octopus auto-detect not started | M, ~60-90k |
| 11. Latest complete cost day in the 22:00 bundle (1.0.12b1) | Done, merged (af7d6ac, PR #30) | XS, ~25k actual |
| 12. Learned household load (+ zero day_actuals guard) (1.0.12b3) | Done, merged (7a1809a, PR #35) | S, ~35k actual |
| 13. Charge losses and in-window load in Flux 1 sizing (1.0.12b3) | Done, merged (2c0833b, PR #37) | XS, ~25k actual |
| 14. Efficiency KPIs and backtest harness (1.0.12b3) | Done, merged (166f29f, PR #38) | M, ~45k actual |
| 15. Day-rate import feedback for the overnight target (1.0.12b3) | Done, merged (18667e7, PR #39) | S, ~25k actual |
| 16. Evening reserve to 02:00 (1.0.12b3) | Done, merged (f739100, PR #40) | S, ~20k actual |
| 17. Sell the expected surplus at the peak rate (1.0.12b3) | Done, merged (c8ba7d9, PR #41) | S, ~30k actual |
| 18. Read tariff rates from the Octopus integration | Done, merged (b4adfc9, PR #43) | S, ~30k actual |
| 19. Pick the full-charge day from the solar forecast (daily re-check) (1.0.12b2) | Done, merged (449434d, PR #32) | S, ~45k actual |
| 20. Faster, seasonal forecast correction (shadow) (1.0.12b2) | Done, merged (6b1702b, PR #33) | XS, ~25k actual |
| 21. Octopus Saving Sessions | Planned, not built | S–M, ~35-60k |
| 22. Charge watchdog (1.0.12b3) | Done, merged (3e59565, PR #36) | S, ~40k actual |
| 23. Gentler charging across the cheap window | Planned, not built | S–M, ~35-60k |
| 24. Battery wear cost in decisions | Planned, not built | S, ~20-40k |
| 25. Spare solar sensor and appliance prompts | Planned, not built | S, ~20-40k |
| 26. Overnight baseload alert | Planned, not built | S, ~20-40k |
| 27. Automatic away mode | Planned, not built | XS–S, ~15-30k |
| 28. Tariff comparison report | Planned, not built | M, ~50-80k |
| 29. Clipped solar detection | Planned, not built | S, ~20-40k |

Phases 12–21 come from the efficiency improvement list (06/10/2026) and are numbered in build order: 12 → 13 → 14 (baseline KPIs) → 15 → 16 → 17; 18–20 independent; 21 after the phase 10 live test. New behaviour ships in shadow/logged form first where it changes inverter writes.

Phases 22–29 come from the second improvement list (06/10/2026; items 26 hot-water diversion and 31 EV battery hold were declined). 22 is independent and recommended early; 23 needs 22 (safety net) and an API check; 24 before 17; 26 shares phase 12's missing-meter guard; 28 and 23 start with a feasibility check and go "On hold" if it fails.

Token actuals were not metered before this index existed; record them from phase 8 on.

## Decisions locked in with the user (do not re-litigate)

- Cost-aware export stays in shadow mode (watt trigger drives) — decided 29/09/2026 (phase 9, option a). Revisit if the cost threshold is tuned or the peak tariff changes.
- Missing cost data is `None`, never 0.
- A logged `daily_cost` value is never overwritten; late readings only fill gaps.
- Low-solar decisions use `min(raw, corrected)` forecast.

## Process (binding)

branch → implement/test → draft PR → green CI → merge → sync main → CI-replica test run (`python3 -m py_compile custom_components/sunsynk_optimizer/*.py && python3 -m pytest`) → update plan docs (this table + the phase file) → stop and report.

Phase files: `phases/NN-short-name.md`. Read a phase file only when working that phase.
