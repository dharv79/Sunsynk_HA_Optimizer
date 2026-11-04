# Phase 75 — Battery state-of-health and degradation ledger

**Status:** Done (04/11/2026). **Size:** M (re-sized from L: real BMS data replaced the fade model). **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

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

## As built (04/11/2026)

**Source.** The user's capture of the portal's Operation data page: `GET /api/v1/workdata/dynamic?page=1&limit=1&sn={sn}&dateRange=D,D&type=1&lan=en` returns the newest ~690-register snapshot (newest first, about one a minute; older pages via `nextStartPrimaryKey`). Keys are `Name(unit)/register`; `planning.parse_battery_health` matches on `Name` only, so renumbering does not break it. Real values on 04/11: SOH 97 % (packs 97.5/97.4), 497/508 cycles, 2 x 104 Ah (10.65 kWh nameplate, 10.38 kWh at pack SOH), cell spread 3-5 mV, cells 12 C, BMS charge limit 128 A, all fault words 0 or `--`.

**What runs.** `optimizer.async_refresh_battery_health` (read-only, so also in monitor mode) is called from `async_check_inverter_events` (2-hour drift check and watchdog recheck). It stores `state.battery_health` = latest snapshot, a compact per-day ledger (`planning.ledger_point`, last 800 days) and `planning.battery_health_trend` (SOH change, fade per year and per 1000 cycles, kWh discharged, measured wear p/kWh vs the configured battery cost / lifetime kWh; rates need >= 30 days). One `battery_health` record per day (deduped type). `sensor.battery_soh` (%, diagnostic) carries the snapshot and trend as attributes.

**Fault words.** Non-zero inverter warning/fault words, BMS fault/alarm words and pack fault/warning words feed `binary_sensor.inverter_fault` as a third source (`inverter_fault_flag(..., health_fault_flag(...))`). Bit meanings are unknown; raw values are logged.

**Charge-stall diagnosis.** The watchdog "charge not running" and the 05:00 "stopped short" alerts now append `planning.bms_summary_line` (SOH, cell temp, spread, BMS limit) plus `bms_stall_hints`: cold cells < 10 C, spread >= 50 mV, BMS charge limit < 50 A, any fault word. Uses the snapshot if under 15 minutes old, else a fresh read.

**Not done (deliberately).** The measured wear cost is shadow only; it does not feed the phase 24 wear term or `arbitrage_worth_it` yet (needs months of ledger). No quarterly report; the trend lives on the sensor and the daily record.
