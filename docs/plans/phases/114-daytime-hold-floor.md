# Phase 114 — Daytime hold floor via the native timer slots

**Status:** Planned, not built. **Size:** M, ~50-80k est. **Origin:** efficiency review, 15/11/2026. Builds on phases 73 (native time-of-use shadow) and 59 (peak hold).

## Goal

Use the inverter's own timer-slot SOC to hold enough battery for the 16:00-19:00 peak on days when solar won't refill it. This replaces day-rate grid top-ups.

## Why (evidence)

Peak-cover top-ups ran on 09/11 and 11/11: the integration bought day-rate energy to make sure the battery covered the peak. On the same days the battery had been discharged into daytime load at the same day rate.

A slot SOC floor stops the battery discharging below the hold level until 16:00. The grid then supplies daytime load directly, so the energy bought is the same, but no top-up has to be pushed through the Flux slots, and no charge-discharge round-trip loss (phase 103) is paid.

The review note on phase 86 still applies: outside the peak, every hour is the same day rate. The saving here is the round-trip loss and fewer cloud writes, not the price.

## Design

- Phase 73 already maps a `peak_hold` (start, SOC) onto the six slots (`planning.tou_slots_from_plan`) in shadow, and logs `tou_shadow` records.
- Pure `planning.daytime_hold_soc(forecast_kwh, peak_need_kwh, capacity_kwh, floor)` returns the hold SOC, or `None` when no hold is needed. It uses the `min(raw, corrected)` forecast (rule 8) and the learned peak load. The hold starts after the morning load peak and ends at 16:00.
- Shadow first: log the hold SOC and the top-up it would have replaced, and add a scorecard row (kWh round-trip saved, top-ups avoided).
- Live behind a `daytime_hold_live` option, using the first `native_tou_live` write path from phase 73:
  - Write only the hold slot's SOC through `_async_write_setting` (rule 5).
  - Read it back.
  - Restore the floor at 16:00.
  - Skip while a free event holds the slots (rule 7).
  - Make no writes in monitor mode (rule 16).
- First step: confirm the slot setting names from the phase 73 shadow log (`readable: true`). The phase goes On hold if they are not confirmed.

## Saving

Unproven, about £5-15 a winter: round-trip loss on 2-4 kWh on hold days, plus fewer pushes.

## Risk

Medium. A stuck hold leaves the battery idle into the evening. Restoring at 16:00 and reading back mitigate this. The drift check (2-hour) re-asserts the floor.

## Acceptance

- Pure logic in a `planning/` module with tests (rule 19). Missing data is `None`, never 0.
- Shadow first. Live only behind the option, monitor-safe, through `_async_write_setting`.
- Docs (`export-control.md` / `import-plan.md`), the plan index and `testing.md` updated.
- py_compile, pyflakes and pytest pass.
