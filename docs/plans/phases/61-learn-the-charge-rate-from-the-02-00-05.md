# Phase 61 — Learn the charge rate from the 02:00/05:00 meter snapshots, and stop derating it twice

**Status:** Planned, not built. **Size:** M. **Category:** optimisation. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Measure the real charge power directly as (grid import − load) between the 02:00 and 05:00 meter_snapshot records, bucketed by battery temperature. This replaces the back-calculation through a hard-coded 3.75 h drain, and the temperature derate is no longer applied on top of a rate that already includes cold nights.

## Why (evidence)

In late October the learned effective rate was 1.59-1.76 kW. The rate actually used was 1.21-1.50 kW because temp_deration_factor (×0.85 at 11-15 °C, ×0.70 at 10 °C) is applied again to a rate learned on equally cold nights (planning.resolve_used_charge_rate:198). The logs, however, show 9.9-10.0 kWh of off-peak import in 3 h with ~0.6 kW of house load, which implies about 2.7 kW into the battery. compute_effective_charge_rate_kw (data_logger.py:652) divides drain by a fixed 3.75 and uses a plain mean. The snapshots needed for a direct measurement have been logged at 02:00 and 05:00 since phase 14 and are not used for this.

## Design

Add planning.measured_charge_kw(snap_02, snap_05, soc_02, soc_05, window_end_h) → battery kWh/h. It only counts the window up to the plan's flux1_end and skips nights where the SOC reached the target, so the measurement is not capped. In data_logger, replace the body of compute_effective_charge_rate_kw with a median over the last 14 qualifying nights, keyed by the temperature bucket from import_plan.battery_temp. In resolve_used_charge_rate, skip temp_deration_factor when the learned bucket matches tonight's temperature. Keep the old method as a fallback until 3 snapshot nights exist. Add a test file row.

## Value

Flux 1 windows on bridge nights become 20-40% shorter and more accurate. It removes a 30-45% underestimate that currently feeds peak-cover charge_minutes (which wait too long) and free-event sizing. Directly it is worth ~£5-10/yr; mainly it is a correctness fix that several decisions depend on.

## Risk

Grid import also includes other loads in the window (e.g. an EV or dishwasher), which inflates the measured charge. Using (import − load) cancels that out. Snapshots logged with soc_from_cache=True should be excluded.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
