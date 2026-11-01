# Phase 54 — Charge outcome record: reached vs target, likely cause and alert

**Status:** Planned, not built. **Size:** S. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

At about 05:05, log a charge_outcome record: target, SOC reached, kWh short, and the likely cause (window ended, taper above about 92%, cold derate, current below the setting, or the inverter stopped early). Notify when the shortfall is more than 5 points. Learn a p50 'achievable max SOC' for the reserve and peak-cover maths, in shadow.

## Why (evidence)

Six of 7 recent 100% nights ended at 94-98% (81% on 29 Oct), and the 12/10 charge stopping at 83% is an open, uninvestigated item. The phase 22 watchdog at 02:20 checks only that charging started. Nothing records whether the charge finished, or why it didn't.

## Design

Add planning.charge_outcome(target, soc_trace, battery_temp_c, flux1_end, rate_kw) that returns the shortfall and a likely-cause label. Sample SOC every 15 min from 02:00 to 05:00, reusing the watchdog recheck timer as transient state. Run the check through _guarded. Write a charge_outcome record, add it to _DEDUP_TYPES, and send '⚠️ Sunsynk: overnight charge stopped short'. Add planning.achievable_max_soc(records) (p50 over 14 days), logged only. Tests per cause.

## Value

Turns the unexplained 2-17 point shortfalls into actionable alerts and answers the open 83% question. A 17-point miss is about 1.7 kWh bought later at the day rate (about £0.25-0.45). The logged achievable max also feeds the drain fix above.

## Risk

The cause is heuristic, so label it 'likely'. Monitor mode is unaffected because this is read-only.

## Vetting note

Merges 'end-of-charge outcome' and 'explain shortfall'. I confirmed charge_watchdog is the only overnight check and that there is no charge-outcome record type. I dropped the taper-fitting part to keep this S.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
