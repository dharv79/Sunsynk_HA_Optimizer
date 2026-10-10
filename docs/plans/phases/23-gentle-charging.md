# Phase 23 — Gentler charging across the cheap window

**Status:** Done, merged (dff4259, PR #45). **Size:** S, ~40k actual. **Origin:** improvement list 2, 06/10/2026 (item 23).

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

## Outcome (09/10/2026)

API check passed: settings at `common/setting/{inverter sn}/read|set`; the grid-charge current `sdBatteryCurrent` is global, not per slot (key inferred from SolarSynkV3 — verify on the first live night). `gentle_charge_current_a` logged on every plan; `gentle_charge_live` (default off) writes it at 01:55, keeps Flux 1 open to 05:00 and restores the original at 05:00 (30-minute retries). Watchdog uses the gentle rate on live nights. Details: `docs/architecture/import-plan.md`.

Follow-up (10/10/2026): the user's Grid Amps is now the ceiling — the live write only ever lowers it (`gentle_current_lowers`), found from the debug data (Grid Amps was 40 A, nameplate maths gave ~58 A).
