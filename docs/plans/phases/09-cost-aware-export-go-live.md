# Phase 9 — Cost-aware export go-live decision

**Status:** Planned, not built. **Size:** S, ~20-40k.

## Scope

Decide whether to turn `CONF_COST_AWARE_EXPORT_SHADOW_MODE` off (cost trigger drives the 16:00–19:00 export-disable). User decision required; shadow mode stays on until then.

## Evidence so far

`cost_aware_export_shadow_tally.divergent_checks` has been 0 every week from 23/08/2026 to 20/09/2026. With default thresholds the cost threshold (58.32 p/h) equals 1.5 kW × 38.88 p/kWh, so zero divergence is expected. Going live only changes behaviour if the user tunes the cost threshold or the peak price changes.

## Design

- Review a few more weekly tallies, then either (a) keep shadow mode and close this phase, or (b) change the default / user option and document it in export-control.md.
- No code change is needed for (a).

## Acceptance criteria

- Decision recorded in plans/README.md "Decisions locked in".
- If (b): export-control.md updated, notification/weekly tally still reported.
