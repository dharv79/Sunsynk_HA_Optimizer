# Phase 117 — Weekly cost without solar and battery

**Status:** Done, merged (cdf187a, PR #229). **Size:** S, ~35k. **Origin:** user request, 16/11/2026.

## Goal

Show what the week would have cost with no solar and no battery, next to what it did cost, and the saving.

## Decisions (user interview, 16/11/2026)

- Shown in: the Sunday notification, `sensor.kpi_no_system_cost` / `sensor.kpi_system_saving` (daily) and their `kpi_week_*` twins, dashboard tiles, and the weekly debug JSON (`week_no_system_gbp`, `week_system_actual_gbp`, `week_system_saving_gbp`, plus `digest.system_value`).
- Priced at the Flux import rate for each band, as if all the house load came from the grid.
- The week is the existing trailing 7 complete days (Sun–Sat), the same period as the weekly summary.
- Two totals and the saving; no solar-versus-battery split (phase 37 already reports the battery share).

## Design

- `planning.no_system_baseline(snapshots, prices, export_bands, free_credit)`: per segment, load × band price without; actual = metered import less export at the same prices. Same 00:00–22:00 snapshot day and free-event handling as phase 37. Missing snapshot or price gives `None` for the day. Battery wear is not deducted.
- Logged on the 22:00 `day_kpis` record and carried to paired days through `DAY_KPI_FIELDS`.
- `planning.weekly_system_value(paired_days)` sums only days with both figures. `system_value_text` is the notification and AI-prompt sentence.

## Limits

00:00–22:00 only (the last meter snapshot is 22:00). Days before 117 have no figures and are excluded, so the first week after install is partial; the sentence states the day count.
