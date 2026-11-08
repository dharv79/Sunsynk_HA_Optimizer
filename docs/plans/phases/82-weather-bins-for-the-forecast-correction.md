# Phase 82 — Weather bins for the forecast correction

**Status:** Planned, not built. **Size:** M, ~40-60k est. **Origin:** idea sweep 08/11/2026 (ideas 2, 3, 4, 20; the remainder of phase 70's "not built" list).

## Goal

Make the overnight target react to the kind of day, not only the forecast total. Learn actual/forecast error by cloud bucket and season, and the load shift by outdoor temperature, then use it for the shadow target and the MPC.

## Vetting (08/11/2026)

- Phase 70 logs P10/P50/P90 by forecast band × month but has no cloud or temperature bins ("weather not on the paired record").
- Phase 71 already has a temperature scale and calendar (weekday/weekend) in the load profile, so cold-weather and day-of-week load are NOT new work.
- Phase 20 and 56 already cover seasonal and intraday correction of the total.

## Design

- Add `cloud_cover_pct` (mean of the forecast hours, from the configured weather entity) and `outdoor_temp_c` to the `import_plan` record and to `_IMPORT_PLAN_FIELDS` (do-not-break 10). Missing → `None`, never 0.
- Extend `data_logger.compute_forecast_quantiles` with a cloud bucket (clear / mixed / overcast). Fall back to the existing band × month bins when a cell has fewer than 5 days.
- Shadow only: log what the target would have been; surface it in the shadow scorecard. The locked `min(raw, corrected)` rule is unchanged (decision 8).
- New decision logic in `planning.py` with tests (do-not-break 19).

## Acceptance

- Tests: bucket edges, sparse-cell fallback, `None` weather, field round-trip through `_pair_records`, scorecard entry.
- No inverter writes.

## Savings estimate (rough, unproven)

Roughly £10-25/yr, mainly on mixed-cloud days where the single factor mis-sizes the charge by 1-2 kWh. Confirm from the scorecard before any live switch. The first winter will be thin.
