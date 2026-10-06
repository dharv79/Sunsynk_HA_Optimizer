# Phase 23 — Gentler charging across the cheap window

**Status:** Planned, not built. **Size:** S–M, ~35-60k est. **Origin:** improvement list 2, 06/10/2026 (item 23).

## Goal

Charge at the lowest current that still reaches the target by window end, instead of full rate then idle — fewer conversion/I²R losses and less battery heat, so fewer kWh bought per kWh stored.

## Design

- **Prerequisite check (first):** confirm the Sunsynk cloud API exposes a writable grid-charge current (and whether it's per-slot or global). If not writable, stop and mark the phase "On hold".
- Pure `planning.gentle_charge_current_a(kwh_needed, window_hours, battery_voltage, max_current_a, margin=1.2, min_current_a)` — margin keeps a buffer for load in the window (phase 13) and calibration error.
- Ship logged-only first: record `gentle_current_a` beside the current full-rate plan in `import_plan`; add to `_IMPORT_PLAN_FIELDS` if paired (do-not-break 10).
- Go-live behind an option (default off); phase 22 watchdog covers a too-slow night. Restore the configured current after the window.
- Phase 14 KPIs compare kWh imported per SOC% gained before/after.

## Acceptance

- Tests for the current maths (clamps, margin, zero need).
- Window sizing (Flux 1 end) unchanged when the option is off.
