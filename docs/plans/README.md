# Plan index

## Context

Sunsynk HA Optimizer 1.0.11 is the latest stable release (Octopus cost link, weekly digest, cost-aware export in shadow mode, optional AI insight, startup re-plan labelling; promoted from 1.0.11b15); 1.0.12b4 (beta) adds phases 11–23 (1.0.12b3 had 11–17, 19, 20 and 22; 1.0.12b2 had 11, 19 and 20). Remaining work is follow-ups surfaced by the `#sunsynkdebug` Slack stream.

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
| 18. Read tariff rates from the Octopus integration (1.0.12b4) | Done, merged (b4adfc9, PR #43) | S, ~30k actual |
| 19. Pick the full-charge day from the solar forecast (daily re-check) (1.0.12b2) | Done, merged (449434d, PR #32) | S, ~45k actual |
| 20. Faster, seasonal forecast correction (shadow) (1.0.12b2) | Done, merged (6b1702b, PR #33) | XS, ~25k actual |
| 21. Octopus Saving Sessions (1.0.12b4) | Superseded by removal (16/10/2026, 1.0.12b16) | M, ~55k actual |
| 22. Charge watchdog (1.0.12b3) | Done, merged (3e59565, PR #36) | S, ~40k actual |
| 23. Gentler charging across the cheap window (1.0.12b4) | Done, merged (dff4259, PR #45) | S, ~40k actual |
| 24. Battery wear cost in decisions | Done, merged (7c8e7b6, PR #48) | S, ~25k |
| 25. Spare solar sensor and appliance prompts | Done, merged (cf0df05, PR #49) | S, ~30k |
| 26. Overnight baseload alert | Done, merged (4fcff5b, PR #50) | S, ~30k |
| 27. Automatic away mode | Done, merged (0f19108, PR #51) | XS–S, ~25k actual |
| 28. Tariff comparison report | Done, merged (92f6a35, PR #53); Agile/Intelligent deferred | M, ~45k |
| 29. Clipped solar detection | Done, merged (cee4395, PR #52) | S, ~30k actual |
| 30. Peak cover (reach 16:00 with enough battery for the peak window) | Done, merged (38d333b, PR #47) | S–M, ~45k actual |
| 31. Inverter drift check | Done, merged (126cfc6, PR #58) | S, ~40k |
| 32. Retry a failed overnight plan push | Done, merged (d1a2a81, PR #57) | XS, ~20k |
| 33. Tariff price-change alert | Done, merged (0731e77, PR #59) | XS, ~15k |
| 34. Battery capacity tracking | Done, merged (2c8963c, PR #66), shadow | S, ~35k |
| 35. Fuller tariff comparison (standing charges, VAT, Agile/Intelligent) | Done, merged (8a92549, PR #60) | M, ~55k |
| 36. Bill reconciliation | Done, merged (dfded0a, PR #67); manual billed entry (no Octopus statement sensor) | S, ~30k |
| 37. Value-of-the-system report | Done, merged (487ba83, PR #65) | S, ~30k |
| 38. "Cost of 1 kWh now" sensor | Done, merged (f790c19, PR #64) | S, ~30k |
| 39. Multi-day low-solar planning | Done, merged (1935017, PR #61), shadow | S, ~30k |
| 40. Daytime load spike alert | Done, merged (33fb1fd, PR #63) | XS, ~20k |
| 41. Shadow go-live scorecard | Done, merged (9eb643f, PR #93) | M, ~40k actual |
| 42. Import cause ledger ("why did I import?") | Planned, not built | M |
| 43. Inverter fault and event log feed (feasibility-gated) | Planned, not built | S |
| 44. CT clamp vs smart-meter reconciliation (1.0.12b31) | Done, merged (9fd7115, PR #90) | XS, ~35k actual |
| 45. Nightly data-integrity audit, HA Repairs and a diagnostics download | Planned, not built | M |
| 46. HA service actions and actionable notifications | Planned, not built | M |
| 47. KPI and money sensors with long-term statistics | Planned, not built | S |
| 48. Grid outage detection, runtime sensor and outage log | Planned, not built | S |
| 49. PV string health monitor (MPPT0 vs MPPT1) | Planned, not built | S |
| 50. Battery and PV upgrade advisor | Planned, not built | M |
| 51. Free events: DST-safe timers, a watch_from guard and sold-energy split | Done, merged (0708689, PR #107) | S, ~45k actual |
| 52. Self-sufficiency KPI that excludes battery grid-charging and free import | Done, merged (32376c0, PR #91) | XS, ~30k actual |
| 53. Measure overnight drain from the 05:00 snapshot SOC, not target_soc | Done, merged (5b70e67, PR #94) | S |
| 54. Charge outcome record: reached vs target, likely cause and alert | Done, merged (9d14d5d, PR #95) | S |
| 55. Evening reserve from learned 16:00-22:00 load (shadow first) | Planned, not built | M |
| 56. Intraday solar re-scaling from PV produced so far | Planned, not built | M |
| 57. API request timeout and catch-up for missed monthly reports | Done, merged (69eb281, PR #97) | XS |
| 58. Plain-English plan explanation on the notification and sensor | Done, merged (eaf3d45, PR #103) | S |
| 59. Peak cover: hold the battery early instead of recharging it at 13:45 | Done, merged (cbec4d4, PR #101; peak cover live option off) | M |
| 60. Peak cover sizing: add wear and stop stacking p75 with a 1.1 margin | Done, merged (883d1dc, PR #100) | S |
| 61. Learn the charge rate from the 02:00/05:00 meter snapshots, and stop derating it twice | Done, merged (96e2022, PR #98; option off) | M |
| 62. Run Flux 1 to 05:00 every night, capped by the target | Done, merged (9680c00, PR #96; option off) | S |
| 63. Remove the 05:00-06:00 double count in drain and bridge | Done, merged (b9e8e85, PR #92); median switch deferred pending backtest | S, ~25k actual |
| 64. Pick the bridge target from the forecast-error distribution (newsvendor rule) | Done, merged (976f1b4, PR #111), shadow only | M, ~45k actual |
| 65. Move trims into the peak window when the solar left today can wait | Planned, not built | M |
| 66. Re-check the peak export plan at 17:00 and 18:00 using the observed peak load | Done, merged (27b831a, PR #102; peak export live option off) | S |
| 67. Cache config and tariff parsing on the SOC-change path | Done, merged (2eeaebf, PR #109) | S, ~40k actual |
| 68. Replace the weekly full-charge day with 'days since the last 100%' | Planned, not built | S |
| 69. Receding-horizon MPC planner (48 h, half-hourly) in shadow | Planned, not built | XL |
| 70. Self-learned probabilistic solar forecast (own P10/P50/P90) | Planned, not built | L |
| 71. Half-hourly household load forecaster (weather and calendar aware) | Planned, not built | L |
| 72. Full digital twin: replay every subsystem on half-hourly history | Planned, not built | XL |
| 73. Native inverter time-of-use programming via the six system-mode slots | Planned, not built | L |
| 74. Local Modbus/RS485 control path as a cloud-outage fallback | Planned, not built | XL |
| 75. Battery state-of-health and degradation ledger | Planned, not built | L |
| 76. Dynamic-tariff native mode (Agile / half-hourly prices in the loop) | Planned, not built | XL |
| 77. Flexibility-market export bidding (non-Saving-Sessions VPP) | Planned, not built | L |
| 78. Household electrification and system-sizing advisor | Planned, not built | L |

Phases 12–21 come from the efficiency improvement list (06/10/2026) and are numbered in build order: 12 → 13 → 14 (baseline KPIs) → 15 → 16 → 17; 18–20 independent; 21 after the phase 10 live test. New behaviour ships in shadow/logged form first where it changes inverter writes.

Phases 31–40 come from improvement list 3 (14/10/2026). Recommended order: 32 → 31 → 33 (protect existing savings), then 35 and 39 (largest new value); 31 and 36 start with a feasibility check (Flux read-back / Octopus statement sensor) and go "On hold" in part if it fails; 36 after 35; 37 and 38 independent; 39 ships in shadow first.

Phases 41–78 come from the idea sweep of 01/11/2026: new features 41–50, improvements 51–58, optimisations 59–68, blue sky 69–78. Features and improvements were vetted against the code; optimisations and blue sky were not (re-vet before building). Two duplicate improvements (diagnostics download, shadow scoreboard) were merged into the matching feature phases. Suggested first builds: CT reconciliation, self-sufficiency KPI fix, 05:00–06:00 double count, shadow scorecard; blue-sky phases need a design pass before any code.

Phase 30 is a user request (13/10/2026). Phases 22–29 come from the second improvement list (06/10/2026; items 26 hot-water diversion and 31 EV battery hold were declined). 22 is independent and recommended early; 23 needs 22 (safety net) and an API check; 24 before 17; 26 shares phase 12's missing-meter guard; 28 and 23 start with a feasibility check and go "On hold" if it fails.

Build order (set 04/11/2026, user request): 51 → 67 → 64 → 55 → 56 → 65 → 68 → 42 → 45 → 47 → 48 → 49 → 46 → 43 → 10 (Octopus auto-detect, on hold) → 75 → 50 → 70 → 71 → 69 → 76 → 73 → 77 → 78 → 72 → 74. Rationale: safety/cheap fixes first (51, 67), then planner accuracy (64, 55, 56, 65, 68), then observability (42, 45, 47-49), then integrations (46, 43, 10), then data foundations for the blue-sky work (75 before 50; 70 and 71 before 69; 69 before 76 and 72; 73 before 74). Optimisation and blue-sky phases (64-78) must be re-vetted against the code before building.

Token actuals were not metered before this index existed; record them from phase 8 on.

## Decisions locked in with the user (do not re-litigate)

- Cost-aware export stays in shadow mode (watt trigger drives) — decided 29/09/2026 (phase 9, option a). Revisit if the cost threshold is tuned or the peak tariff changes.
- Missing cost data is `None`, never 0.
- A logged `daily_cost` value is never overwritten; late readings only fill gaps.
- Low-solar decisions use `min(raw, corrected)` forecast.

## Process (binding)

branch → implement/test → draft PR → green CI → merge → sync main → CI-replica test run (`python3 -m py_compile custom_components/sunsynk_optimizer/*.py && python3 -m pytest`) → update plan docs (this table + the phase file) → stop and report.

Phase files: `phases/NN-short-name.md`. Read a phase file only when working that phase.
