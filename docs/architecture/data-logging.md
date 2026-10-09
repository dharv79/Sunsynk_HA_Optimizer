# Data logging and adaptive learning

JSONL record types, dedup, pairing, the four compute_* corrections, home/away split.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

- **`data_logger.py`** records decisions and actuals to monthly JSONL files at `{config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl`. Six record types: `import_plan` (at 01:55), `morning_state` (at 06:00 — SOC, PV power, and `overnight_load_kwh` before solar starts), `day_actuals` (at 22:00 — evening SOC, actual solar kWh, plus `day_load_kwh`/`day_grid_import_kwh`/`day_grid_export_kwh` from the SolarSynkV3 daily totals), `peak_window_usage` (16:00–19:00 load/grid delta, logged by the optimizer's peak-window tracker above), `daily_cost` (settled real cost from the optional Octopus Energy sensors — see below), `full_charge_day` (weekly scores), `charge_watchdog` (02:20 overnight-charge check, one per night — see import-plan.md), and the phase 14 `meter_snapshot` (02:00/05:00/16:00/19:00/22:00, dedup per date+time) and `day_kpis` (22:00) records — see below. The load/grid fields exist so consumption review doesn't have to infer household load from the evening-SOC swing, which conflates load with solar availability — `day_load_kwh`/`overnight_load_kwh` are real energy totals, not SOC-derived. They're carried into the paired-day dict by `_pair_records` (read as `None` on history that predates each field) but no `compute_*` method consumes them yet — currently informational/for manual review only (`peak_window_usage` in particular: nothing auto-tunes `CONF_EXPORT_DISABLE_THRESHOLD` from it). Per-day record types (including `peak_window_usage` and `charge_watchdog`; `daily_cost` merges instead — see octopus-cost.md) are deduplicated at write time (`_write_record` calls `_record_exists` before appending) to prevent double entries on HA restarts that cross a scheduled-event boundary. Provides four analysis methods used by `optimizer.py` to apply adaptive corrections:
- `compute_forecast_correction` — **median** (not mean) of actual/forecast ratios over 30 days, capped 0.5–3.0, requires 7+ days. Median so one anomalous day (tiny forecast, huge actual → unbounded ratio) can't jolt the factor. The denominator is the stored *corrected* forecast, which makes the update self-damping: at equilibrium the factor settles at sqrt(true raw bias) — deliberate under-correction, the safe direction (planner expects less solar than arrives → charges more).
- `compute_soc_target_adjustment` — ±5% nudge based on evening SOC outcomes, requires 5+ matching non-high-solar days.
- `compute_overnight_drain_adjustment` — p75 (`_DRAIN_PERCENTILE`) of overnight drain, extra % to target SOC to compensate battery drain before 06:00, requires 5+ valid days, 15% fallback below that. Qualifying nights are defined by the shared `_is_drain_night` predicate, which requires a **real overnight charge** (`initial_soc < target_soc`) — no-charge nights (battery already above target, just discharging from a high start) are excluded so they can't be mistaken for post-charge drain.
- `compute_effective_charge_rate_kw` — kW from historical charge sessions (needs a night with `target − initial ≥ 10%`), requires 3+ days, else `None`. When it returns `None`, `optimizer.py` reuses the last learned rate instead of the nameplate config: fallback chain is fresh computation → persisted `OptimizerState.last_effective_charge_rate_kw` → `last_known_charge_rate_kw(paired_days)` (most recent non-null rate in history, seeds the persisted value on first run). Summer high-SOC nights rarely reach the 10% gap, so this fallback is the normal path much of the year.

`count_*` progress counters mirror their compute predicates (drain uses the same `_is_drain_night`), except `count_soc_adjustment_days`, which intentionally counts all in-band days regardless of solar level while the nudge computation still filters out high-solar days (`_HIGH_SOLAR_THRESHOLD_KWH = 15.0`). Files older than 13 months are pruned on startup via `coordinator.py`.

**Home/away calibration split.** Each plan runs in an occupancy regime — `away = coordinator.state.away_mode` (toggled by the built-in **Away mode** switch, `switch.py`, persisted in `OptimizerState`). The drain (`compute_overnight_drain_adjustment` / `_is_drain_night`) and evening-nudge (`compute_soc_target_adjustment`) computations and their counters take an `away` parameter and filter to days whose logged `away` flag matches, so a low-load holiday learns its own profile and can't skew the home one (and vice versa). Charge-rate calibration excludes away days entirely (the rate is physical; away nights back-calculate through an atypical drain), so when away it reuses the home-learned rate via the normal fallback chain. Forecast correction stays global (load-independent). Each `import_plan` record is tagged with `away` (the persisted field list is `_IMPORT_PLAN_FIELDS` in `data_logger.py` — any plan field `_pair_records` reads back must be listed there); `_pair_records` carries it into the paired dict (default `False`, so all pre-1.0.9 history reads as home). The solar-bridge consumption figure is also regime-aware: `avg_consumption_kw` becomes `CONF_AWAY_AVG_CONSUMPTION_KW` (default 0.3 kW) when away — away takes precedence over the weekday/weekend split — so the bridge and synthetic-ramp targets size to the low holiday load. The away drain buffer also has its own lower default (`_DEFAULT_DRAIN_ADJUSTMENT_AWAY = 8` vs home 15) until 5 away nights accumulate.


## Efficiency KPIs and backtest (09/10/2026, phase 14)

**Goal.** Measure whether phases 12–20 reduce bought energy, and tune them offline before they go live.

**Snapshots.** `meter_snapshot` records the cumulative SolarSynkV3 daily `grid_import_kwh`, `grid_export_kwh` and `load_kwh`, plus `soc`, at the Flux band edges.

- 02:00, 05:00, 16:00 and 19:00 come from their own listeners, run through `_guarded`.
- 22:00 is taken inside `_async_capture_day_actuals`.
- Snapshots are read-only, so they also run in monitor mode. An unavailable meter logs `None`.
- Dedup is per date and time: `_record_exists` also matches `time`, which only snapshots carry.

**KPIs.** `planning.day_kpis(snapshots, prices, capacity, late_load_kw)` covers 00:00–22:00. The daily meters reset at midnight, so 22:00–24:00 is not counted, the same as `day_actuals`.

- `grid_import_offpeak_kwh` covers 02:00–05:00, `grid_import_peak_kwh` covers 16:00–19:00, and `grid_import_day_kwh` covers the rest. `grid_import_morning_kwh` (05:00–16:00, part of the day band) was added in phase 15, and `grid_import_evening_kwh` (19:00–22:00) plus `evening_reserve_soc` on the record in phase 16.
- `self_sufficiency_pct` is `1 − import / load` at 22:00.
- `avoidable_import_gbp` is day plus peak import at their prices.
- `export_peak_kwh` and `export_peak_gbp` cover 16:00–19:00.
- `unused_charge_kwh` is the overnight SOC added that was still spare at 16:00. The need is 20% reserve plus 16:00–22:00 load in hindsight plus 4 h at tonight's plan load rate, and the result is capped at what the night added.
- Prices come from the configured `charges` via `flux_helpers.kpi_prices_pence` (`band_price_pence_per_kwh`, generalised from the peak-price helper).
- Any missing snapshot, meter, price, or meter that went backwards gives `None` for that KPI, never 0.

**Reporting.** A `day_kpis` record (deduped) is logged at 22:00 and added to the 22:00 bundle. `_pair_records` carries the KPI fields into paired days. The Sunday digest adds `planning.week_kpis`: kWh and £ summed, self-sufficiency averaged, plus `week_kpi_days`.

**Backtest.** Run `python3 tools/backtest.py DATA_DIR [--target-offset N] [--load-kw X] [--capacity K] [--days N]`. It is HA-free and read-only, and loads `planning.py` / `flux_helpers.py` by path.

- It replays logged `import_plan` + `morning_state` home nights. Moving the target moves charge-end and 06:00 SOC.
- Off-peak kWh is charged SOC divided by the logged `charge_efficiency`. The day-rate shortfall is 06:00 SOC below the 20% reserve.
- It reports baseline vs alternative kWh and £.
- `--load-kw` re-sizes plain `solar_bridge` nights that logged `hours_to_solar`. That field was added to `_IMPORT_PLAN_FIELDS` for this, so older nights only take the offset.
- It also reports the forecast MAE of the live vs weighted (phase 20) correction.

**Verification.** `tests/test_kpis.py` covers the bands, prices, unused charge cap, `None` on missing inputs, the week roll-up, snapshot dedup and pairing, and the backtest against a fixture data dir including the CLI. In HA, the 22:00 bundle gains a `day_kpis` line once all five snapshots exist (the first full day after install).
