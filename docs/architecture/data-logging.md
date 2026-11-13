# Data logging and adaptive learning

JSONL record types, dedup, pairing, the four compute_* corrections, home/away split.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

- **`data_logger.py`** records decisions and actuals to monthly JSONL files at `{config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl`. Record types include (the full deduped set is `DataLogger._DEDUP_TYPES`; later phases add more, see the dated sections): `import_plan` (at 01:55), `morning_state` (at 06:00 — SOC, PV power, and `overnight_load_kwh` before solar starts), `day_actuals` (at 22:00 — evening SOC, actual solar kWh, plus `day_load_kwh`/`day_grid_import_kwh`/`day_grid_export_kwh` from the SolarSynkV3 daily totals), `peak_window_usage` (16:00–19:00 load/grid delta, logged by the optimizer's peak-window tracker above), `daily_cost` (settled real cost from the optional Octopus Energy sensors — see below), `full_charge_day` (weekly scores), `charge_watchdog` (02:20 overnight-charge check, one per night — see import-plan.md), and the phase 14 `meter_snapshot` (02:00/05:00/16:00/19:00/22:00, dedup per date+time) and `day_kpis` (22:00) records — see below — `peak_export` (16:00 decision, phase 17, see export-control.md) `peak_export_recheck` (17:00/18:00 re-check, phase 66, not deduped, see export-control.md) `peak_cover` (midday top-up decision, phase 30, one per day, see export-control.md), `inverter_drift` (phase 31, one per new drift episode, see export-control.md), `inverter_event` and `grid_notice` (phase 43, one per new realtime event key or cloud notice id, deduped by the caller via state), `battery_health` (phase 75, one per day, deduped: BMS SOH, packs, cycles, cells, limits, fault words and trend) `capacity_estimate` (phase 34, monthly, deduped, see import-plan.md) and `multi_day_plan` (phase 39 shadow, per stage inside a low-solar run, see import-plan.md; neither is deduped). The load/grid fields exist so consumption review doesn't have to infer household load from the evening-SOC swing, which conflates load with solar availability — `day_load_kwh`/`overnight_load_kwh` are real energy totals, not SOC-derived. They're carried into the paired-day dict by `_pair_records` (read as `None` on history that predates each field) but no `compute_*` method consumes them yet — currently informational/for manual review only (`peak_window_usage` in particular: nothing auto-tunes `CONF_EXPORT_DISABLE_THRESHOLD` from it). Per-day record types in `_DEDUP_TYPES` (including `peak_window_usage` and `charge_watchdog`; `daily_cost` merges instead — see octopus-cost.md) are deduplicated at write time (`_write_record` calls `_record_exists` before appending) to prevent double entries on HA restarts that cross a scheduled-event boundary. Provides four analysis methods used by `optimizer.py` to apply adaptive corrections:
- `compute_forecast_correction` — **median** (not mean) of actual/forecast ratios over 30 days, capped 0.5–3.0, requires 7+ days. Median so one anomalous day (tiny forecast, huge actual → unbounded ratio) can't jolt the factor. The denominator is the stored *corrected* forecast, which makes the update self-damping: at equilibrium the factor settles at sqrt(true raw bias) — deliberate under-correction, the safe direction (planner expects less solar than arrives → charges more).
- `compute_soc_target_adjustment` — ±5% nudge based on evening SOC outcomes, requires 5+ matching non-high-solar days.
- `compute_overnight_drain_adjustment` — p75 (`_DRAIN_PERCENTILE`) of overnight drain, extra % to target SOC to compensate battery drain before 06:00, requires 5+ valid days, 15% fallback below that. Qualifying nights are defined by the shared `_is_drain_night` predicate, which requires a **real overnight charge** (`initial_soc < target_soc`) — no-charge nights (battery already above target, just discharging from a high start) are excluded so they can't be mistaken for post-charge drain.
- `compute_effective_charge_rate_kw` — kW from historical charge sessions (needs a night with `target − initial ≥ 10%`), requires 3+ days, else `None`. When it returns `None`, `optimizer.py` reuses the last learned rate instead of the nameplate config: fallback chain is fresh computation → persisted `OptimizerState.last_effective_charge_rate_kw` → `last_known_charge_rate_kw(paired_days)` (most recent non-null rate in history, seeds the persisted value on first run). Summer high-SOC nights rarely reach the 10% gap, so this fallback is the normal path much of the year.

`count_*` progress counters mirror their compute predicates (drain uses the same `_is_drain_night`), except `count_soc_adjustment_days`, which intentionally counts all in-band days regardless of solar level while the nudge computation still filters out high-solar days (`_HIGH_SOLAR_THRESHOLD_KWH = 15.0`). Files older than 13 months are pruned on startup via `coordinator.py`.

**Home/away calibration split.** Each plan runs in an occupancy regime — `away = coordinator.state.away_mode` (toggled by the built-in **Away mode** switch, `switch.py`, persisted in `OptimizerState`). The drain (`compute_overnight_drain_adjustment` / `_is_drain_night`) and evening-nudge (`compute_soc_target_adjustment`) computations and their counters take an `away` parameter and filter to days whose logged `away` flag matches, so a low-load holiday learns its own profile and can't skew the home one (and vice versa). Charge-rate calibration excludes away days entirely (the rate is physical; away nights back-calculate through an atypical drain), so when away it reuses the home-learned rate via the normal fallback chain. Forecast correction stays global (load-independent). Each `import_plan` record is tagged with `away` (the persisted field list is `_IMPORT_PLAN_FIELDS` in `data_logger.py` — any plan field `_pair_records` reads back must be listed there); `_pair_records` carries it into the paired dict (default `False`, so all pre-1.0.9 history reads as home). The solar-bridge consumption figure is also regime-aware: `avg_consumption_kw` becomes `CONF_AWAY_AVG_CONSUMPTION_KW` (default 0.3 kW) when away — away takes precedence over the weekday/weekend split — so the bridge and synthetic-ramp targets size to the low holiday load. The away drain buffer also has its own lower default (`_DEFAULT_DRAIN_ADJUSTMENT_AWAY = 8` vs home 15) until 5 away nights accumulate.


## Efficiency KPIs and backtest (09/10/2026, phase 14)

**Goal.** Measure whether phases 12–20 reduce bought energy, and tune them offline before they go live.

**Snapshots.** `meter_snapshot` records the cumulative SolarSynkV3 daily `grid_import_kwh`, `grid_export_kwh` and `load_kwh`, plus `soc`, at the Flux band edges. With no live SOC, the last reading up to 30 minutes old is used and `soc_from_cache` is true (01/11/2026, see import-plan.md).

- 02:00, 05:00, 16:00 and 19:00 come from their own listeners, run through `_guarded`.
- 22:00 is taken inside `_async_capture_day_actuals`.
- Snapshots are read-only, so they also run in monitor mode. An unavailable meter logs `None`.
- Dedup is per date and time: `_record_exists` also matches `time`, which only snapshots carry.

**KPIs.** `planning.day_kpis(snapshots, prices, capacity, late_load_kw)` covers 00:00–22:00. The daily meters reset at midnight, so 22:00–24:00 is not counted, the same as `day_actuals`.

- `grid_import_offpeak_kwh` covers 02:00–05:00, `grid_import_peak_kwh` covers 16:00–19:00, and `grid_import_day_kwh` covers the rest. `grid_import_morning_kwh` (05:00–16:00, part of the day band) was added in phase 15, and `grid_import_evening_kwh` (19:00–22:00) plus `evening_reserve_soc` on the record in phase 16.
- `self_sufficiency_pct` (phase 52, `kpi_version` 2) is `1 − (import − free-event import − 02:00-05:00 battery gain) / load` at 22:00, `None` without the 02:00/05:00 SOC. `grid_independence_pct` is the old `1 − import / load`. Pre-v2 records carry no `kpi_version`.
- `avoidable_import_gbp` is day plus peak import at their prices, less over-limit import (see below).
- `export_peak_kwh` and `export_peak_gbp` cover 16:00–19:00.
- `unused_charge_kwh` is the overnight SOC added that was still spare at 16:00. The need is 20% reserve plus 16:00–22:00 load in hindsight plus 4 h at tonight's plan load rate, and the result is capped at what the night added.
- Prices come from the configured `charges` via `flux_helpers.kpi_prices_pence` (`band_price_pence_per_kwh`, generalised from the peak-price helper).
- Any missing snapshot, meter, price, or meter that went backwards gives `None` for that KPI, never 0.

**Reporting.** A `day_kpis` record (deduped) is logged at 22:00 and added to the 22:00 bundle. `_pair_records` carries the KPI fields into paired days. The Sunday digest adds `planning.week_kpis`: kWh and £ summed, self-sufficiency averaged, plus `week_kpi_days`. Since phase 37, `day_kpis` also carries the battery-vs-no-battery fields (see weekly-reports.md).

**Backtest.** Run `python3 tools/backtest.py DATA_DIR [--target-offset N] [--load-kw X] [--capacity K] [--days N]`. It is HA-free and read-only, and loads `planning.py` / `flux_helpers.py` by path.

- It replays logged `import_plan` + `morning_state` home nights. Moving the target moves charge-end and 06:00 SOC.
- Off-peak kWh is charged SOC divided by the logged `charge_efficiency`. The day-rate shortfall is 06:00 SOC below the 20% reserve.
- It reports baseline vs alternative kWh and £.
- `--load-kw` re-sizes plain `solar_bridge` nights that logged `hours_to_solar`. That field was added to `_IMPORT_PLAN_FIELDS` for this, so older nights only take the offset.
- It also reports the forecast MAE of the live vs weighted (phase 20) correction.

**Verification.** `tests/test_kpis.py` covers the bands, prices, unused charge cap, `None` on missing inputs, the week roll-up, snapshot dedup and pairing, and the backtest against a fixture data dir including the CLI. In HA, the 22:00 bundle gains a `day_kpis` line once all five snapshots exist (the first full day after install).

## Clipped solar (13/10/2026, phase 29)

Option `export_limit_w` (0 = off). The 30-min tick (`optimizer._async_track_clipped_solar`) counts a period as clipped when SOC ≥ 99 and export ≥ 95% of the limit (`planning.is_clipping`; `grid_pac < 0` is export) and adds `estimate_clipped_kwh` (hourly forecast − PV from the two MPPT sensors, floored at 0; `None` if either is missing, never 0). The tally is unpersisted. `day_actuals` gains `clipped_solar_kwh` and `overnight_target_too_high` (clipped and first seen full before 11:00), both passed through `_pair_records`; the weekly digest carries `week_clipped_solar_kwh`. No inverter writes; phases 15/20 may consume the flag later.

## Over-limit import left out of avoidable £ (20/10/2026)

**Root cause.** On 19/10 the house drew up to about 10 kW in bursts between 08:20 and 09:00 with the battery at about 85%. The grid covered what the inverter could not supply. All 2.0 kWh of morning import counted as avoidable (£0.84 in total), although no plan could have shifted it.

**Design.**
- `optimizer._async_track_over_limit_import` samples SOC and the daily import meter on every SOC change, the 30-min tick and each meter snapshot. The snapshot sample closes the interval at the band edge.
- For each pair of samples, `planning.over_limit_import_step` counts the import as over-limit when:
  - both SOCs are above `KPI_RESERVE_SOC` + 5;
  - the SOC fell, so the battery was already discharging into the house;
  - the samples are at most 35 minutes apart.
- It doesn't count when:
  - the SOC is flat, because a slot target may be holding the battery;
  - the SOC is rising, because that's a grid charge;
  - a value is unknown;
  - the import meter has reset.
- The kWh goes to the segment (`kpi_segment`) where the interval starts.
- The tally is unpersisted, like `_clipped`. After a restart, import already tallied that day is counted as avoidable again (the conservative way). It's skipped while a free event holds the slots, because that import is credited separately.
- `day_kpis(..., over_limit=)` takes each segment's over-limit kWh (capped at that segment's import) out of the day and peak parts of `avoidable_import_gbp`. The import bands still include it, because it was paid for. A new field, `over_limit_import_kwh`, is logged.

**Verification.** `tests/test_kpis.py::test_over_limit_import_step` and `test_day_kpis_leave_over_limit_import_out_of_avoidable`. In HA, `day_kpis` gains `over_limit_import_kwh` from the first full day after install. Records already logged stay as written.

## Version stamp (29/10/2026)

**Why.** A missing field in the debug bundle could mean either a bug or an older build still running, and the logs could not tell which.

**Design.** `const.INTEGRATION_VERSION` is read once from `manifest.json` at import (`None` if unreadable). `data_logger._record` adds `version` to every JSONL record. The 01:55 plan and the 22:00 `day_kpis` record also carry it, so every 22:00 debug bundle names the running build.

**Verification.** `tests/test_data_logger.py::test_records_carry_the_manifest_version`. Live: the next 22:00 bundle shows `"version": "1.0.12b29"`.

## Drain measured to 05:00 (01/11/2026, phase 63)

The solar bridge counts load from 05:00, so the drain buffer must stop at 05:00 too. Paired days carry `overnight_drain_pct_0500` (`planning.drain_pct_to_0500`): from the 05:00 meter snapshot SOC, else the 06:00 drain scaled by (window-1)/window. `compute_overnight_drain_adjustment` uses it when `_DRAIN_USE_0500` is True. p75 and nearest-5 rounding are unchanged pending a backtest.

## Charge-shortfall nights excluded from drain (01/11/2026, phase 53)

`target - SOC@05:00` is drain only if the charge reached target. `planning.charge_shortfall_suspect` flags a gap above 3 points + 5 points per hour between Flux 1 end and 05:00; `_pair_records` stores `charge_short` and `drain_source`, and `compute_overnight_drain_adjustment` skips flagged nights (`_DRAIN_EXCLUDE_SHORT`) while at least 5 clean nights remain. Heuristic thresholds; tune from live `charge_short` counts.

## Charge outcome record (01/11/2026, phase 54)

`charge_outcome` (one per night, deduped) is written after the 05:00 meter snapshot: target, reached SOC (the snapshot), `shortfall_pct`, `likely_cause`, `alert`, and `achievable_max_soc` (14-day median on target >= 95 nights, logged only). A shortfall above 5 points notifies. Cause is a heuristic label.

## Import causes (04/11/2026, phase 42)

`import_causes` (22:00, one per day, in `_DEDUP_TYPES`): kWh per cause plus `attributed_kwh` against the day's `grid_import_kwh`; the gap is import never sampled. Causes come from `planning.import_cause_step`; 'unknown' is explicit, never guessed. `sensor.import_causes_today` shows the running tally.

## Data audit, outage log and PV strings (04/11/2026, phases 45, 48, 49)

- **Audit (45).** 06:20 `async_run_data_audit` runs `planning.audit_days` over the last 14 complete days (from the first logged day): missing `import_plan`/`morning_state`/`day_actuals`/`day_kpis`, `daily_cost` (only with an Octopus import cost sensor, after a 2-day settle), duplicates, and contradictions (0% self-sufficiency or 0 kWh load against 5+ kWh solar). One HA Repairs issue per class, deleted when it clears. The only write is the fill-only `daily_cost` backfill (rule 12). `diagnostics.py` is the redacted download.
- **`outage` (48).** One record per closed grid outage (start, end, minutes, SOC start/low/end, battery kWh served). Not deduped. While an outage is open, plan, Flux 2, peak cover/export and retry pushes return early.
- **`pv_strings` (49).** One per day, deduped: MPPT0/MPPT1 kWh sampled 10:00-15:00 above 1 kW (battery not full), MPPT0 share, rolling median, anomaly flag.
- **`user_override` (46).** Tag on `import_plan` (in `_IMPORT_PLAN_FIELDS`); such nights are excluded from drain and evening-nudge learning. `_pair_records` copies `user_override` and `cloud_cover_pct` into the paired day (phase 87, 11/11/2026): before that neither reached the paired days, so override nights were learned as normal and the phase 82 cloud bins always saw `cloud=None`. Any plan field a paired-day reader uses must be copied in `_pair_records` (rule 10 covers only the logged list); `test_paired_day_carries_*` guards both.

## MPC plan record (05/11/2026, phase 69)

`mpc_plan` (every 30 min, not deduped, never read back by learning): date, time, soc, cost_gbp, first_action, first_soc, soc_0500, grid_charge_kwh, offpeak_charge_kwh, day_charge_kwh, export_kwh, horizon_slots, rule_plan_date, rule_target_soc, loads_kw, tomorrow_solar_kwh. See `import-plan.md`.

## Load profile fields (05/11/2026, phase 71)

`day_kpis` gains `outside_temp_c` (mean of the 30-min weather-entity samples that day; None after a restart with no samples) and `evening_reserve_soc_profile`; `peak_cover` gains `peak_load_kwh_profile`; `mpc_plan` gains `load_profile_days`. The heating fit reads `outside_temp_c` back from `day_kpis`.

## Dynamic tariff record (05/11/2026, phase 76)

`dynamic_tariff` (22:05, deduped by date and time, never read back): date, time, region, soc, flux_cost_gbp, agile_cost_gbp, saving_gbp, flux/agile_grid_charge_kwh, agile_export_kwh, agile_soc_0500, horizon_slots, min_p, max_p, spike_slots, negative_slots, cheapest_windows, cheapest_avg_p, tomorrow_prices. When the run is skipped it carries only date, time, region and `skipped`. See `import-plan.md`.

## TOU shadow record (05/11/2026, phase 73)

`tou_shadow` (after the 01:55 plan, deduped by date and time, never read back): date, time, target_soc, proposed (six {time, soc, grid_charge}), inverter (same shape or None), readable, diffs [{slot, field, inverter, plan}] or None, diff_count. See `import-plan.md`.

## Flex event record (05/11/2026, phase 77)

`flex_event` (once per event start, deduped by date and start time, never read back): date, time (event start), entity, hours, soc, price_p, house_load_kw, decision, offer_kwh, value_gbp, gain_gbp, floor_soc. See `export-control.md`.

## Log read performance (12/11/2026, phase 91)

**Root cause.** Every read re-parsed the monthly files (one 01:55 plan parsed about 25 files), `_pair_records` rescanned all records once per date on the event loop, dedup-at-write parsed the current month per write, and `mpc_plan` (48 lines a day, never read back) was about two-thirds of the log.

**Design.**
- `DataLogger._load_file` keeps each monthly file parsed in memory, keyed by size and mtime. Past months are parsed once per run; `_append_line` keeps the current month in step, and a file changed by anything else is re-read. `_read_recent` returns copies, so callers cannot alter the cached records.
- `snapshots_by_date` indexes the meter snapshots in one pass; `_pair_records` and `async_load_snapshot_days` use it. `async_load_paired_days` reads and pairs inside the executor job.
- Dedup-at-write uses a `(type, date, time)` set held with the parsed file, and also checks the file for the month of the record's own date, so it still holds after a month roll.
- `mpc_plan` records go to `mpc-YYYY-MM.jsonl` (`_SIDE_FILE_PREFIX`). The pruner removes those files on the same 13-month rule, `tools/backtest.py` skips them, and older `mpc_plan` lines in the main log are skipped on read.

**Not built.** Passing `paired_days` down through the callbacks and memoising the peak-cover inputs: the cache makes repeat reads cheap without changing any caller.

**Verification.** `tests/test_data_logger.py` (phase 91 block): index equals the per-date scan, one parse until the file changes, copies and window, dedup incl. the previous month, the `mpc_plan` file, the pruner. Synthetic 395-day log: paired read 0.32 s cold, 0.03 s warm; a 30-day read 2.5 ms warm; about 10 MB held.

## CSV export service (13/11/2026, phase 109)

`sunsynk_optimizer.export_data` writes the logged records as CSV (one file per record type plus a daily summary) to `<config>/sunsynk_optimizer_export/<timestamp>/`. The type list is `data_logger.RECORD_TYPES`, so a new record type must be added there (already required). See `docs/plans/phases/109-data-export-service.md`.
