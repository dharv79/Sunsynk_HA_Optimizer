# Phase 17 — Sell the expected surplus at the peak rate

**Status:** Planned, not built. **Size:** M, ~50-80k est. **Origin:** improvement list 06/10/2026 (item 5).

## Goal

Replace the fixed 82% trim with a computed 16:00 target: evening reserve (phase 16) plus margin; everything above is exported 16:00–19:00 at the Flux peak rate.

## Design

- Pure `planning.peak_export_target(soc_16, evening_reserve_soc, margin, export_price, import_price_offpeak)`; export only when the peak export price exceeds the off-peak replacement cost ÷ round-trip efficiency.
- Pushes via Flux 2 export window (direction=0, index 1 — do-not-break 3) through `_async_post_with_status`; skipped while a free event holds the slots (do-not-break 7) and in monitor mode (16).
- Interaction with the 16:00–19:00 export-disable (watt trigger, shadow mode locked — do not change that decision): export-disable still wins when it fires.
- Shadow first: log intended target/kWh/£ without pushing; go live behind a config flag.

## Acceptance

- Tests: surplus → target, no surplus → no export, price not worth it → no export, free event blocks.
- Weekly digest shows shadow peak-export £.
