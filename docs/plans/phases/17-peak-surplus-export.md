# Phase 17 — Sell the expected surplus at the peak rate

**Status:** Done, merged (c8ba7d9, PR #41). **Size:** S, ~30k actual.

**As built:**

- The 16:00 listener uses `planning.peak_export_plan`: the target is the reserve + 5%, and it exports only a surplus of at least 0.5 kWh. `planning.arbitrage_worth_it` compares the export price with off-peak ÷ 0.85, plus a wear term.
- It was built before phase 24 at the user's request (13–17 in one run). The wear term is a `None` hook for phase 24 to fill.
- Shadow by default. The `peak_export_live` option pushes Flux 2 16:00–19:00. Trims are skipped while a live export holds the slot, the export-disable still wins, and free events and monitor mode never push.
- A `peak_export` record goes into the 22:00 bundle, pairing and the weekly digest.

Details: `docs/architecture/export-control.md`. **Origin:** improvement list 06/10/2026 (item 5).

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
