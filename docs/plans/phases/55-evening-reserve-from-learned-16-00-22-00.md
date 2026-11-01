# Phase 55 — Evening reserve from learned 16:00-22:00 load (shadow first)

**Status:** Planned, not built. **Size:** M. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Learn the household's evening load rate from the 16:00, 19:00 and 22:00 meter_snapshot load_kwh, with a weekday/weekend split. Use it in evening_reserve_soc and the trim floor, instead of the overnight learned_load_kw.

## Why (evidence)

evening_reserve_soc (planning.py:877) multiplies _plan_load_kw (the plan's avg_consumption_kw, learned from 00:00-06:00 load in learned_load_kw) by 7 h. Winter evening load is well above the overnight baseload. The 22:00 SOC sat on the 25% floor on 4 of 7 days, with evening imports of 1.1 and 1.9 kWh.

## Design

Add planning.learned_band_load_kw(snapshot_days, '16:00', '22:00', weekend) as a 28-day median, needing at least 7 days and clamped like resolve_load_kw, using data_logger.async_load_snapshot_days. Log both reserves on day_actuals and in flux2 actions. Switch with a new evening_load_live option (default off). It changes trim writes, so it must start in shadow. Tests against a snapshot fixture.

## Value

About 1-2 kWh less evening day-rate import on affected winter days, roughly £0.25-0.55 per day or about £20-50/yr. The summer cost is a few fewer trims, which the shadow log will show.

## Risk

A higher reserve means fewer trims and less export on sunny days. Compare in shadow and in backtest before going live.

## Vetting note

Confirmed. _evening_reserve_soc uses _plan_load_kw, which comes from overnight learned load, and snapshots at 16, 19 and 22 exist. This merges two duplicate candidates and keeps the *_live gate the binding rules require.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
