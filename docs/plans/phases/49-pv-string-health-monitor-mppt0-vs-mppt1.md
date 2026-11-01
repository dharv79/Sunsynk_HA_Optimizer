# Phase 49 — PV string health monitor (MPPT0 vs MPPT1)

**Status:** Planned, not built. **Size:** S. **Category:** new feature. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Learn the normal power split between the two MPPT strings on bright hours. Alert when one string drifts well below its usual share for several days (shading, soiling, a failed connector) before the forecast correction quietly absorbs the loss.

## Why (evidence)

`pv_mppt0_entity` and `pv_mppt1_entity` (optimizer.py:381–382) are only ever summed (lines 1835, 2375, 2430). A dead string looks like a bad forecast to the learned correction, which already moves between 1.224 and 1.395 in a week and can range from 0.5 to 3.0, so a fault would also skew learning silently.

## Design

Sample both MPPT powers in the existing 30-minute tick when total PV is above about 1 kW. Keep per-hour sums in a transient dict. At 22:00, add `mppt0_kwh`/`mppt1_kwh` to day_actuals. Add `planning.string_ratio_anomaly(history, today)` with tests, comparing same-hour ratios against a rolling 14-day median and MAD. Notify after 3 consecutive anomalous days. Add a diagnostic ratio sensor. Single-MPPT systems are a no-op.

## Value

A failed half-array of about 2 kWp loses roughly 800–1,700 kWh/yr, about £80–250 at a blend of export and avoided import. Detection takes days instead of months.

## Risk

East/west or differently pitched strings change their ratio with hour and season, so compare like-for-like hours with a short rolling window.

## Vetting note

Two candidates merged. Confirmed the MPPT entities are read and only summed. The fix is cheap and catches a high-cost fault that the forecast correction would otherwise mask.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
