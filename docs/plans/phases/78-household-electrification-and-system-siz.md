# Phase 78 — Household electrification and system-sizing advisor

**Status:** Done, report only (see As built). **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Use 13 months of logged half-hourly import, solar, clipping, unused charge and gas cost to simulate upgrades: a second battery module, more panels, a bigger inverter export limit, or a heat pump on Cosy versus gas. It produces payback periods from the household's own data.

## Why (evidence)

Gas is about 36% of the bill (£9.64 of £26.88 in the 18–24 Oct week; £3.00 on 26 Oct). The battery saved only £1.76 that week, self-sufficiency was 6.9–36.5% in late October, and 22:00 SOC hit the floor on 4 of 7 days, which points to capacity-limited winters. `clipped_solar_kwh` and `unused_charge_kwh` are logged but nothing acts on them. Hot-water diversion was declined, so this is advice only and controls nothing.

## Design

Add a scenario layer to the twin idea: `tools/twin.py --scenario battery+5kWh` and `--scenario pv+2kWp` (scaling logged solar, adding back clipping), plus a heat-pump scenario that converts gas kWh by COP and outside temperature onto Cosy prices. Output a yearly £ delta and simple payback. An annual HA notification and a JSON line to `data_report_target` summarise it.

## Value

Decision support for £2k–£12k purchases. Avoiding one wrong-sized upgrade is worth more than all the runtime tweaks combined.

## Risk

The models are approximate (heat-pump COP, PV scaling). It must state confidence clearly. It depends on the twin.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (05/11/2026)

- Feasibility: no locked decision or do-not-break rule touched, and nothing is controlled. It builds on the phase 50 `simulate_system` advisor instead of the phase 72 twin, which isn't built. The battery and PV scenarios are already there; this adds the heat-pump and inverter-limit scenarios.
- `heat_pump_cop`, `mean_band_price`, `heat_pump_scenario` (always "low" confidence, with its assumptions), `export_limit_scenario` and `electrification_summary`.
- Options `gas_unit_pence`, `gas_standing_pence` and `heat_pump_price_gbp`.
- Wired into the monthly upgrade advisor (report, debug JSON and notification).
- Not built: `tools/twin.py --scenario`, which waits for phase 72.
