# Plan index

## Context

Sunsynk HA Optimizer is on the 1.0.11 beta line (Octopus cost link, weekly digest, cost-aware export in shadow mode, optional AI insight). Latest prerelease: 1.0.11b14. Remaining work is follow-ups surfaced by the `#sunsynkdebug` Slack stream.

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

Token actuals were not metered before this index existed; record them from phase 8 on.

## Decisions locked in with the user (do not re-litigate)

- Cost-aware export stays in shadow mode (watt trigger drives) — decided 29/09/2026 (phase 9, option a). Revisit if the cost threshold is tuned or the peak tariff changes.
- Missing cost data is `None`, never 0.
- A logged `daily_cost` value is never overwritten; late readings only fill gaps.
- Low-solar decisions use `min(raw, corrected)` forecast.

## Process (binding)

branch → implement/test → draft PR → green CI → merge → sync main → CI-replica test run (`python3 -m py_compile custom_components/sunsynk_optimizer/*.py && python3 -m pytest`) → update plan docs (this table + the phase file) → stop and report.

Phase files: `phases/NN-short-name.md`. Read a phase file only when working that phase.
