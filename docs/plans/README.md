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
| 42. Import cause ledger ("why did I import?") | Done, merged (7a23093, PR #119), shadow; weekly digest breakdown not built | M, ~40k actual |
| 43. Inverter fault and event log feed (feasibility-gated) | Done, merged (9ab4058, PR #134) | S, ~60k actual |
| 44. CT clamp vs smart-meter reconciliation (1.0.12b31) | Done, merged (9fd7115, PR #90) | XS, ~35k actual |
| 45. Nightly data-integrity audit, HA Repairs and a diagnostics download | Done, merged (77ecb00, PR #121) | M, ~35k actual |
| 46. HA service actions and actionable notifications | Done, merged (e7c8e6b, PR #125); `pause_export_until` and notification action buttons not built | M, ~35k actual |
| 47. KPI and money sensors with long-term statistics | Done, merged (79562e0, PR #122); weekly sensors and dashboard card not built | S, ~25k actual |
| 48. Grid outage detection, runtime sensor and outage log | Done, merged (0f86a4a, PR #123); needs a live `grid_status` sample | S, ~30k actual |
| 49. PV string health monitor (MPPT0 vs MPPT1) | Done, merged (d2cf87c, PR #124) | S, ~25k actual |
| 50. Battery and PV upgrade advisor | Done, merged (5765373, PR #140), report only | M, ~45k actual |
| 51. Free events: DST-safe timers, a watch_from guard and sold-energy split | Done, merged (0708689, PR #107) | S, ~45k actual |
| 52. Self-sufficiency KPI that excludes battery grid-charging and free import | Done, merged (32376c0, PR #91) | XS, ~30k actual |
| 53. Measure overnight drain from the 05:00 snapshot SOC, not target_soc | Done, merged (5b70e67, PR #94) | S |
| 54. Charge outcome record: reached vs target, likely cause and alert | Done, merged (9d14d5d, PR #95) | S |
| 55. Evening reserve from learned 16:00-22:00 load (shadow first) | Done, merged (aae95db, PR #113), shadow only | M, ~35k actual |
| 56. Intraday solar re-scaling from PV produced so far | Done, merged (aff091d, PR #115), shadow + advisory | M, ~25k actual |
| 57. API request timeout and catch-up for missed monthly reports | Done, merged (69eb281, PR #97) | XS |
| 58. Plain-English plan explanation on the notification and sensor | Done, merged (eaf3d45, PR #103) | S |
| 59. Peak cover: hold the battery early instead of recharging it at 13:45 | Done, merged (cbec4d4, PR #101; peak cover live option off) | M |
| 60. Peak cover sizing: add wear and stop stacking p75 with a 1.1 margin | Done, merged (883d1dc, PR #100) | S |
| 61. Learn the charge rate from the 02:00/05:00 meter snapshots, and stop derating it twice | Done, merged (96e2022, PR #98; option off) | M |
| 62. Run Flux 1 to 05:00 every night, capped by the target | Done, merged (9680c00, PR #96; option off) | S |
| 63. Remove the 05:00-06:00 double count in drain and bridge | Done, merged (b9e8e85, PR #92); median switch deferred pending backtest | S, ~25k actual |
| 64. Pick the bridge target from the forecast-error distribution (newsvendor rule) | Done, merged (976f1b4, PR #111), shadow only | M, ~45k actual |
| 65. Move trims into the peak window when the solar left today can wait | Done, merged (634ea94, PR #117), shadow (`trim_defer_live` off) | M, ~35k actual |
| 66. Re-check the peak export plan at 17:00 and 18:00 using the observed peak load | Done, merged (27b831a, PR #102; peak export live option off) | S |
| 67. Cache config and tariff parsing on the SOC-change path | Done, merged (2eeaebf, PR #109) | S, ~40k actual |
| 68. Replace the weekly full-charge day with 'days since the last 100%' | Done, merged (643fd4c, PR #118), shadow (`full_charge_skip_live` off) | S, ~25k actual |
| 69. Receding-horizon MPC planner (48 h, half-hourly) in shadow | Done, merged (f860226, PR #141), shadow; 24 h horizon, `mpc_live` not built | L, ~55k actual |
| 70. Self-learned probabilistic solar forecast (own P10/P50/P90) | Done, merged (f5a86eb, PR #142), shadow; no weather bins | M, ~35k actual |
| 71. Half-hourly household load forecaster (weather and calendar aware) | Done, merged (2ee29b0, PR #143), shadow (`load_profile_live` off) | M, ~45k actual |
| 72. Full digital twin: replay every subsystem on half-hourly history | Planned, not built | XL |
| 73. Native inverter time-of-use programming via the six system-mode slots | Done, merged (98b3280, PR #149), shadow; no writes | M, ~30k actual |
| 74. Local Modbus/RS485 control path as a cloud-outage fallback | Planned, not built | XL |
| 75. Battery state-of-health and degradation ledger | Done, merged (3b4ddb5, PR #137) | M, ~70k actual |
| 76. Dynamic-tariff native mode (Agile / half-hourly prices in the loop) | Done, merged (bb5b6fb, PR #148), shadow; control stays on Flux | M, ~40k actual |
| 77. Flexibility-market export bidding (non-Saving-Sessions VPP) | Done, merged (c6a3488, PR #150), shadow; nothing exported | M, ~25k actual |
| 78. Household electrification and system-sizing advisor | Done, merged (1291c33, PR #151), report only | M, ~30k actual |
| 79. Own Sunsynk power-flow card (replaces slipx06; user request 05/11/2026) | Done, merged (1101fb0, PR #156) | L, ~120k actual |
| 80. Day chart tab with previous/next day navigation (user request 05/11/2026) | Done, merged (93af8a5, PR #159) | M |
| 81. Vulnerability and security sweep (user request 06/11/2026) | Done, merged (a94c43f, PR #170) | M, ~60k |
| 82. Weather bins for the forecast correction | Done, merged (88e32c7, PR #179), shadow only | M, ~40-60k |
| 83. High grid voltage export guard (feasibility-gated) | Done, merged (fb8c934, PR #181), tally only | S, ~25-35k |
| 84. Battery low-SOC floor review (reworked 10/11/2026) | Done, merged (fba41c1, PR #180), read-only | S, ~20-30k |
| 85. Prorated bill reconciliation and missing-days alert | Done, merged (728fd3e, PR #176) | XS-S, ~20-30k |
| 86. Daytime import and reserve-hold review | Done, merged (ade154e, PR #182), shadow only | S, ~25-40k |
| 87. Paired-day field fixes (cloud cover and user override) | Done, merged (42c5f9f, PR #186) | XS, ~25k actual |
| 88. Listener guards, 22:00 ordering and a pure dry run | Done, merged (45271d3, PR #194) | S, ~40k actual |
| 89. Send the debug bundle as whole JSON lines | Done, merged (6572656, PR #190) | XS, ~25k actual |
| 90. Flux write safety (slot carry-over and serialised decisions) | Done, merged (bf2f1da, PR #198) | S, ~45k actual |
| 91. Log read performance | Done, merged (8e9ad86, PR #201) | S-M, ~50k actual |
| 92. Fewer state writes on SOC changes | Done, merged (1a0effc, PR #200) | S, ~30k actual |
| 93. Options validation and diagnostics redaction | Done, merged (02f1444, PR #199) | S, ~40k actual |
| 94. Stable entity IDs and long-term statistics (feasibility-gated) | Done, merged (c62f699, PR #202) | S, ~40k actual |
| 95. Card rendering: redraw only on change | Done, merged (04cded7, PR #203) | S, ~35k actual |
| 96. Release gate (tests and version check before a release) | Done, merged (ea89659, PR #196) | XS, ~25k actual |
| 97. Refactor 1: shared helpers, dead code and missing tests | Built (PR pending); one percentile/median, shared HH:MM parse, `_mpc_inputs`, `_async_weather_forecast`, `_async_outside_temp_by_date`, `grid_online` removed; load-window and weighted-ratio dedup deferred | S-M, ~40-60k |
| 98. Refactor 2: split planning.py into a package | Planned, not built | M-L, ~70-110k |
| 99. Refactor 3: split optimizer.py | Planned, not built | L, ~120-175k |
| 100. Daily health line and deploy notice | Planned, not built | S, ~25-40k |
| 101. Cloud API reliability log | Planned, not built | S, ~20-35k |
| 102. Shiftable-load report | Planned, not built | M, ~40-60k |
| 103. Measured round-trip efficiency (shadow) | Planned, not built | S, ~30-45k |
| 104. Weekly digest: import causes, charge shortfalls and shadow results | Planned, not built | S, ~25-40k |
| 105. Weather-normalised load report | Planned, not built | S-M, ~30-50k |
| 106. Import heat-map card (hour by day) | Planned, not built | M, ~45-70k |
| 107. Inverter settings snapshot and change audit | Planned, not built | S, ~25-40k |
| 108. Notification priorities, quiet hours and a morning brief | Planned, not built | M, ~40-60k |
| 109. Data export service (CSV) | Planned, not built | S, ~20-35k |
| 110. Marginal-forecast target blend and bridge margin | Done, merged (29e8028, PR #188) | XS, ~35k actual |
| 111. Recent-nights floor on the learned overnight load | Done, merged (9ba6bb8, PR #192) | XS, ~30k actual |

Phases 12–21 come from the efficiency improvement list (06/10/2026) and are numbered in build order: 12 → 13 → 14 (baseline KPIs) → 15 → 16 → 17; 18–20 independent; 21 after the phase 10 live test. New behaviour ships in shadow/logged form first where it changes inverter writes.

Phases 31–40 come from improvement list 3 (14/10/2026). Recommended order: 32 → 31 → 33 (protect existing savings), then 35 and 39 (largest new value); 31 and 36 start with a feasibility check (Flux read-back / Octopus statement sensor) and go "On hold" in part if it fails; 36 after 35; 37 and 38 independent; 39 ships in shadow first.

Phases 41–78 come from the idea sweep of 01/11/2026: new features 41–50, improvements 51–58, optimisations 59–68, blue sky 69–78. Features and improvements were vetted against the code; optimisations and blue sky were not (re-vet before building). Two duplicate improvements (diagnostics download, shadow scoreboard) were merged into the matching feature phases. Suggested first builds: CT reconciliation, self-sufficiency KPI fix, 05:00–06:00 double count, shadow scorecard; blue-sky phases need a design pass before any code.

Phase 30 is a user request (13/10/2026). Phases 22–29 come from the second improvement list (06/10/2026; items 26 hot-water diversion and 31 EV battery hold were declined). 22 is independent and recommended early; 23 needs 22 (safety net) and an API check; 24 before 17; 26 shares phase 12's missing-meter guard; 28 and 23 start with a feasibility check and go "On hold" if it fails.

Build order (set 04/11/2026, user request): 51 → 67 → 64 → 55 → 56 → 65 → 68 → 42 → 45 → 47 → 48 → 49 → 46 → 43 (reopened) → 10 (Octopus auto-detect, on hold) → 75 → 50 → 70 → 71 → 69 → 76 → 73 → 77 → 78 → 72 → 74. Rationale: safety/cheap fixes first (51, 67), then planner accuracy (64, 55, 56, 65, 68), then observability (42, 45, 47-49), then integrations (46, 43, 10), then data foundations for the blue-sky work (75 before 50; 70 and 71 before 69; 69 before 76 and 72; 73 before 74). Optimisation and blue-sky phases (64-78) must be re-vetted against the code before building.

Phases 82–85 come from the savings idea sweep of 08/11/2026 (20 ideas, excluding tariff, heat pump and EV ideas, vetted against the code). 14 were already built (phases 13, 17, 20, 23, 24, 25, 26, 27, 29, 37, 40, 49, 56, 61, 62, 66, 70, 71, 75); the immersion/solar diverter idea stays declined (list 2, item 26). The rest became 82–85. Rough savings, unproven: 82 £10-25/yr, 83 £0-10/yr, 84 £5-15/yr, 85 none direct. Suggested order: 85 → 82 → 84 → 83 (superseded by the deploy order below).

**Deploy order for the remaining phases (set 10/11/2026, review of the Oct-Nov statement and logs): 82 → 84 → 83 → 86 (all done 10/11/2026) → 10 → 72 → 74.** 82 first because it only logs weather fields and its shadow scoring needs weeks of data to accumulate. 84 next: the log review shows the 25% evening floor is a hard limit (probably the inverter's own setting), so the original learned-floor design would save nothing; it is now a read-only check with a £ estimate. 83 is a cheap feasibility scan (On hold if no derated days). 86 is low priority: outside 16:00-19:00 all hours are the same day rate, so reserve timing costs nothing while the battery is cycled fully each day. 10 (Octopus auto-detect) stays parked as tariff-related. 72 then 74 are XL, 74 last because it adds a local write path (81 is done). Further savings found: none big left in software on dark days, because the 11.4 kWh battery is already filled each night by the 3-hour window; the remaining levers are the inverter low-SOC setting (84), promoting peak-export from shadow to live (scorecard ready, +£2.41 over 22 days, but no surplus in winter, so a summer gain), and the winter load itself.

Phases 87–109 come from the code review and idea sweep of 11/11/2026 (user request: check the code, optimise, refactor, and 10 new ideas for efficiency, visibility and reporting). 87–96 are review findings, each verified against the code (87's two bugs and 93's bad-time crash were reproduced by running the functions; 90 and 94 are medium confidence and start with a re-vet). 97–99 are the refactor, behaviour-preserving, in three steps. 100–109 are new ideas, checked only for duplicates against phases 1–86 and for the excluded topics (tariff, heat pump, EV, diverter); vet each against the code before building. **Suggested build order: 87 → 89 → 88 → 96 → 90 → 93 → 92 → 91 → 94 → 95 → 97 → 98 → 99 → 100 → 101 → 104 → 103 → 102 → 105 → 107 → 109 → 108 → 106 → 10 → 72 → 74.** 87 first because phase 82 (released in 2.0.2) cannot use its cloud-cover data until it is fixed. The fixes come before the refactor so they are made once; the refactor comes before the new features so those land in the new layout. 101 before 74 because it measures whether the local Modbus path is needed.

Phase 81 (security sweep, user request 06/11/2026) is independent and recommended before 72 and 74, because 74 adds a new local write path to the inverter.

Token actuals were not metered before this index existed; record them from phase 8 on.

## Decisions locked in with the user (do not re-litigate)

- Cost-aware export stays in shadow mode (watt trigger drives) — decided 29/09/2026 (phase 9, option a). Revisit if the cost threshold is tuned or the peak tariff changes.
- Missing cost data is `None`, never 0.
- A logged `daily_cost` value is never overwritten; late readings only fill gaps.
- Low-solar decisions use `min(raw, corrected)` forecast.

## Process (binding)

branch → implement/test → draft PR → green CI → merge → sync main → CI-replica test run (`python3 -m py_compile custom_components/sunsynk_optimizer/*.py && python3 -m pytest`) → update plan docs (this table + the phase file) → stop and report.

Phase files: `phases/NN-short-name.md`. Read a phase file only when working that phase.
