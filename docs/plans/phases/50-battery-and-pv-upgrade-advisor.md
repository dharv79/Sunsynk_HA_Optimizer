# Phase 50 — Battery and PV upgrade advisor

**Status:** Done (see As built). **Size:** M. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

A monthly 'what if' report that replays logged days with +5 or +10 kWh of battery, or +N kWp of PV. It reports the £ each would have saved, the months where it matters, and the payback at a user-entered price.

## Why (evidence)

The battery is the winter bottleneck. The 22:00 SOC sat on the 25% floor on 4 of 7 days, 5.6–6.2 kWh/day was imported at 27.77p on 30–31 Oct, and the target was 100% on 6 of 7 nights. Phase 37's `no_battery_baseline` (planning.py:2186) models only battery vs no battery and never prices a bigger system.

## Design

Generalise the `_BASELINE_SEGMENTS` simulator (planning.py:2163) into `simulate_system(snapshots, capacity_kwh, charge_kw, pv_scale, prices, rte, wear_p)`. Add a test that it reproduces phase 37's numbers at the current size. Run it from the monthly 18:00 dispatcher next to `_async_send_monthly_battery_value` over the last 30–90 paired days. Add a CLI mode in `tools/backtest.py`. Post a JSON line plus a summary ('+5 kWh would have saved £X in October; payback Y yrs'). Add optional `CONF_UPGRADE_PRICE_PER_KWH`.

## Value

Shifting about 4–6 kWh/day from 27.77p to off-peak (net of about 85% round-trip efficiency, about 8p/kWh) over about 150 winter days comes to roughly £50–90/yr. The real value is grounding a £1.5–4k purchase in the household's own data.

## Risk

The segment-level simulation ignores in-segment dynamics and charge-rate limits, so it needs caveats. Winter-only data understates PV upgrades, so the report must say which months it covers.

## Vetting note

Two candidates merged (sizing advisor and upgrade advisor). Confirmed the phase 37 baseline model exists to extend. I corrected the claimed £90–120/yr down for round-trip losses. Kept for variety: it is decision support rather than ops monitoring.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (05/11/2026)

- `planning.simulate_system` (0 kWh = phase 37 baseline, tested), `upgrade_scenarios`, `upgrade_advisor`, `upgrade_summary`.
- Monthly with the other reports; JSON `upgrade_advisor` line + notification; `state.upgrade_advisor`.
- Options: `upgrade_price_per_kwh`, `pv_kwp` (enables the +2 kWp scenario), `pv_upgrade_price_per_kwp`.
- `tools/backtest.py --upgrade`. Details: `docs/architecture/weekly-reports.md`.
