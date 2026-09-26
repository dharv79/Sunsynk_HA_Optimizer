# Do-not-break: reasoning

CLAUDE.md keeps the one-line rule; the "why" lives here. Numbering matches CLAUDE.md.

1. **Read config only via `merge_entry_data`** (`flux_helpers.py`). Settings may live in `entry.data` (initial setup) or `entry.options` (reconfiguration); reading either directly silently ignores the other. See config.md.
2. **`plant_id` ≠ `inverter_serial`.** `plant_id` is the numeric Sunsynk API station ID used in every API call; `inverter_serial` builds SolarSynkV3 entity IDs (`sensor.solarsynkv3_{serial}_…`). Swapping them breaks either the API or every sensor read.
3. **Flux window indices are fixed** (`flux_helpers.apply_flux_override`): index 0 = Flux 1 import (`direction=1`), index 1 = Flux 2 export (`direction=0`). The income POST body depends on this order.
4. **State changes only via `coordinator.update_state`** (`coordinator.py`). It persists via `Store` and triggers `async_set_updated_data`; entities are event-driven with no polling, so a direct mutation is neither saved nor shown.
5. **API pushes only via `_async_post_with_status`** (`optimizer.py`), and notification text is gated on its bool so a failed push is reported as "NOT applied" rather than as success.
6. **Scheduled callbacks run through `_guarded`** (`optimizer.py`), so an exception lands in `last_error` instead of escaping into the event loop as a silent nightly failure.
7. **Reload one-shot is skipped 16:00–19:00 while `evening_export_disabled`** (`optimizer.py`). The 60 s post-setup plan re-pushes Flux 2, which would re-enable export mid-pause.
8. **Low-solar decisions key on `min(raw, corrected)`** (`planning.select_target_soc`). The learned uplift comes mostly from good days; on a bad day the raw forecast is right, and multiplying it above 7 kWh would skip the max-import override. See import-plan.md.
9. **Drain nights require a real charge (`initial_soc < target_soc`)** (`data_logger._is_drain_night`). No-charge nights discharge from a high start and would be mistaken for post-charge drain.
10. **Any plan field `_pair_records` reads back must be in `_IMPORT_PLAN_FIELDS`** (`data_logger.py`). Unlisted fields are not persisted and read back as `None` (the 1.0.11b8 import_plan field-logging bug).
11. **Missing cost is `None`, never 0** (`planning.net_cost_gbp`). A missing sensor treated as zero would look like a real zero-cost day and corrupt year-to-date.
12. **`daily_cost` merges fill-only and is dated per sensor** (`data_logger.merge_daily_cost_fields`, `optimizer._read_octopus_previous_day_cost`). Never overwrite a logged value, never log under the wrong date; gas often settles a day after electricity. See octopus-cost.md.
13. **Other per-day record types dedup at write** (`data_logger._write_record`, `_DEDUP_TYPES`). HA restarts that cross a scheduled boundary would otherwise double-log and corrupt pairing.
14. **`cost_trigger` stays `None` when no price applies** (`flux_helpers.peak_import_price_pence_per_kwh`, `optimizer.async_run_flux2_check`). Coercing to a 0p price would make the cost trigger never fire.
15. **Shadow mode defaults on: `watt_trigger` drives export-disable** (`CONF_COST_AWARE_EXPORT_SHADOW_MODE`). Keeps upgrades behaviour-neutral until the user opts in. See export-control.md.
16. **Monitor mode makes no API writes** (`optimizer.py` early returns in the three main logic paths).
17. **Test plan button is a pure dry run** (`async_run_import_plan(dry_run=True)`): no inverter push, no log write, no state change. The old push buttons pushed daytime SOC readings to the inverter.
18. **Away days are filtered by regime** (`data_logger` drain/nudge compute + counters; charge-rate excludes away days). A low-load holiday must not skew the home profile or the physical charge rate.
19. **New decision logic goes in `planning.py` with a test** — keeps it HA-free and unit-testable (`tests/test_planning.py`).
