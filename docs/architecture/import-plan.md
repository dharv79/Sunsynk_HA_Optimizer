# Import plan and full-charge day

01:55 plan, target SOC selection, Flux 1 window sizing, full-charge-day scoring.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### Import plan logic

Runs nightly at 01:55. Begins with a pre-flight check: battery SOC and forecast sensor must be available. If either is unavailable the plan is skipped and `last_error` is set. The forecast sensor has a fallback: if unavailable but a prior `raw_forecast_kwh` exists in `last_import_plan`, it is reused (with no correction factor applied).

Before calculating targets, four adaptive corrections are fetched from `data_logger.py` (each returns a neutral value until enough history exists):

1. **Forecast correction factor** — raw forecast kWh × the median actual/forecast ratio from the last 30 days (see `compute_forecast_correction` above).
2. **Overnight drain adjustment** — p75 extra % added to `target_soc` to compensate for battery drain between charge end and 06:00, measured only on real-charge nights.
3. **Evening SOC adjustment** — ±5% nudge to `target_soc` based on whether the battery has been ending the day too full or too empty (high-solar days excluded from the nudge direction but counted toward the progress threshold).
4. **Effective charge rate** — calibrated (or last-known, see above) kW rate used to size the Flux 1 window precisely.

**`low_solar_forecast_kwh` = min(raw, corrected).** All low-solar decisions (the `< 7 kWh` target override, the full-charge-day bridge-vs-grid choice, and the extend-window-to-05:00 rule) key on this pessimistic value, not the corrected forecast. The learned uplift is derived mostly from good days; on a genuinely bad day the raw forecast is already right, and multiplying it above 7 kWh would skip the max-import override and leave the battery short. If either forecast says a bad day, believe it.

**SOC target selection:**
- **Full charge day** (selected weekly best day): if `low_solar_forecast_kwh ≥ 7` and `sun.sun` is available, uses solar bridge target (same as regular days) so solar charges the battery to 100% during the day for free. Falls back to grid-to-100% if forecast is poor.
- **Low solar (`low_solar_forecast_kwh < 7`)**: winter_like → 100%, other bands → 95%.
- **Solar bridge** (normal path when `sun.sun` available): `target = 20 + (hours_to_solar × avg_consumption / battery_capacity) × 100`, clamped 30–100%.
- **Band fallback** (no `sun.sun`): summer_like → 80%, shoulder → 85%, winter_like → 95%.

Drain and evening-nudge adjustments are applied whenever `target_soc < 100` (covers both regular nights and full-charge-day solar bridge plans). Skipped when target is already 100%.

Import window (Flux 1) is physics-based: `minutes = (energy_needed_kwh / charge_efficiency / used_charge_rate) × 60` (phase 13), rounded up to the next 15-minute slot, clamped 02:15–05:00. Extended to 05:00 when `low_solar_forecast_kwh < 7`. `used_charge_rate = min(config charge rate, effective_charge_rate)`, then multiplied by a battery-temperature deration factor.

All API pushes go through `_async_post_with_status()` which returns a bool. On failure the notification title switches to a "⚠️ Sunsynk: … NOT applied" variant. On a successful push the import plan clears any stale `last_error`. The plan state dict always includes `api_ok`, `source`, `forecast_fallback`, `low_solar_forecast_kwh`, and `charge_rate_from_cache` fields. Notification titles follow a `🔋 Sunsynk: <sentence case>` convention.

### Scoring logic (full-charge day)

Scores Monday–Friday from weather forecast: base score `100 - cloud_coverage - (rain_prob * 0.7)`, adjusted by condition string (+25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow), temperature (±3), and day-of-week penalty (Thursday −5, Friday −15). Highest score wins.

### Startup re-plan vs nightly plan (03/10/2026, 1.0.11b15)

Every startup/reload runs a full plan (`_async_initial_refresh`) and pushes it. It was labelled `source: "automatic"` like the 01:55 run, and the 22:00 data report posted `last_import_plan`, so a daytime reload replaced the 01:55 plan in `#sunsynkdebug` (seen 30/09–02/10: plan SOC 60–82% vs 32–47% the previous evening).

- Startup runs now carry `source: "startup"` (`planning.STARTUP_PLAN_SOURCE`).
- `planning.should_log_import_plan`: a startup plan before 01:55 is not written to the JSONL log, since first-per-date dedup would otherwise pair it instead of the real 01:55 plan. Later startup plans log normally (dedup keeps the 01:55 record; if HA was down at 01:55 the startup plan is the one the inverter ran).
- `OptimizerState.nightly_import_plan` holds the last non-startup plan; `planning.daily_report_plans` posts it first at 22:00 and appends a same-day startup re-plan as a second line.

Verification: `tests/test_planning.py` (`should_log_import_plan` cut-off, `daily_report_plans` ordering/dedup/stale date); the 22:00 post shows `source: "startup"` lines only after a reload.

### Daily full-charge-day re-check (06/10/2026, phase 19)

Forecast.Solar only gives today and tomorrow, so the Sunday pick can only score Tue–Fri by weather. Every day at 18:00 (after the Sunday pick on Sundays), `async_recheck_full_charge_day` may move this week's full-charge day to tomorrow:

- `tomorrow_kwh` = `min(raw, raw × forecast correction)` from `tomorrow_forecast_sensor` (default `sensor.energy_production_tomorrow`; blank disables).
- `planning.full_charge_day_move` moves only if: tomorrow is a weekday before the chosen day; not already moved this week (`OptimizerState.full_charge_day_moved_to`, cleared by the Sunday pick); `tomorrow_kwh ≥ planning.solar_kwh_to_fill` (battery capacity + 8 h × avg load); and tomorrow's fresh weather score beats the chosen day's. Any missing input → no move.
- Every check that reaches the decision logs a `full_charge_recheck` record (moved + reason); a move notifies "🔋 Sunsynk: full-charge day moved". Skipped in monitor mode.

Verification: `tests/test_planning.py` (`full_charge_day_move` reasons, threshold, `solar_kwh_to_fill`); on a sunny-tomorrow evening before a cloudier chosen day the move notification fires and the "Selected full charge day" sensor shows tomorrow.

## Weighted forecast correction, shadow (06/10/2026, phase 20)

**Problem.** The live correction is the plain median of actual/forecast over 30 paired days, so it lags about two weeks behind spring/autumn changes and mixes sunny and dull days.

**Design.** `planning.weighted_forecast_correction(paired_days, today, band)` weights each day `0.5 ** (age / 14)` and takes the weighted median. It uses only days in tonight's forecast band when there are at least 7, otherwise all days (`basis` "global"); below 7 days overall it returns `(1.0, "none")`. Same 0.5–3.0 cap and >0.5 kWh forecast filter as `data_logger.compute_forecast_correction`.

**Shadow.** The 01:55 plan logs `forecast_correction_weighted` and `forecast_correction_weighted_basis` beside `forecast_correction_factor` (both in `_IMPORT_PLAN_FIELDS`, so the JSONL record keeps them for the backtest); the live factor is unchanged. Switch over only after the phase 14 backtest shows the weighted factor tracks actual solar better. Low-solar decisions keep `min(raw, corrected)` either way (do-not-break 8). Solcast P10 was not added (Forecast.Solar is the configured source).

**Verification.** `tests/test_planning.py` (min days, recency, band and fallback, cap, bad rows). In HA: after 01:55 the `import_plan` debug line carries both factors.

## Learned household load (07/10/2026, phase 12)

**Design.** The solar bridge (`bridge_soc`, `walk_bridge_gap`) used a fixed `avg_consumption_kw` from config (weekday/weekend/away). Home nights now use a rate learned from the 06:00 `morning_state.overnight_load_kwh`, which is the SolarSynkV3 daily load at 06:00, so it covers 00:00–06:00:

- `planning.learned_load_kw(paired_days, today, weekend)` takes the median of `overnight_load_kwh / 6` over the last 28 complete days. It needs at least 7 days, otherwise it returns `None`.
- Days that are excluded:
  - away days (do-not-break 18);
  - full-charge days;
  - days with a missing or zero load;
  - today.
- The weekday/weekend split uses only matching days when there are 7 of them, otherwise all days.
- `planning.resolve_load_kw` clamps the learned rate to 0.5×–2× the matching config rate. With no learned rate it falls back to config.
- Away nights always use the config away rate.

**Prerequisite fix.** A meter unavailable at 22:00 or 06:00 used to log `0.0` load, like the zeroed 28/09 `day_actuals`. It now logs `None`: `_async_capture_day_actuals` and `_async_capture_morning_state` read the meters with `_essential_state`. Zero and `None` days are skipped by the learning.

**Logged.** The plan sets `avg_consumption_kw` (the rate used, which the dashboard reads) and also logs:

- `load_source` (`learned` / `config`);
- `learned_load_kw` (before the clamp, `None` when there is too little history);
- `learned_load_days`.

The first three fields are in `_IMPORT_PLAN_FIELDS`, so the JSONL record and the 22:00 bundle plan line both carry them. They are not read back by pairing.

**Verification.** `tests/test_planning.py` covers the minimum day count, the median, the exclusions, the weekend split with its fallback, and the clamp. `tests/test_day_actuals.py` checks that missing meters log `None`.


## Charge watchdog (07/10/2026, phase 22)

Catches a night where the Flux 1 charge silently fails (cloud write lost, inverter ignored it).

- **When:** 02:20 listener (`CHARGE_WATCHDOG_MINUTES`), run through `_guarded`. Skipped in monitor mode, while a free event holds the slots, when tonight's 01:55 plan (`nightly_import_plan`) never pushed (`api_ok is None`), when no charge was planned (`target_soc <= soc`), or when the window has already ended.
- **Judgement:** pure `planning.charge_progress_ok(start_soc, now_soc, minutes, expected_rate_kw, capacity_kwh, grid_import_w, target_soc)` → `ok | stalled | unknown`. `ok` when SOC rose by at least 25% of the expected rise (1% floor), grid import is at least 50% of the expected charge power (SOC lags the cloud poll), or SOC reached target. `unknown` on any missing sensor — never acted on.
- **On stalled:** re-push the plan's payload once via `async_push_flux_override` → `_async_post_with_status`, then re-check 15 min later (`async_call_later`, handle cancelled on shutdown, unpersisted), judged from the retry-time SOC over the minutes the window was still open. Still stalled → "⚠️ Sunsynk: overnight charge not running" with SOC, target, window and grid figures; wording gated on the re-push bool.
- **Log:** `charge_watchdog` record (`result` ok / unknown / recovered / stalled, `retried`, `retry_api_ok`, `soc_start`, `soc_now`, `grid_import_w`, `first_check`); in `_DEDUP_TYPES`, so one per night.
- **Verification:** `tests/test_planning.py` (rising SOC, flat with import, flat without, missing sensors, target reached, 1% floor); `tests/test_day_actuals.py` dedup. Live behaviour needs a real failed night; the listener path is HA-only.


## Charge losses and in-window load (09/10/2026, phase 13)

**Problem.** Flux 1 was sized as if every imported kWh landed in the battery, and the plan never said how much grid energy the night would buy.

**Check first.** `compute_effective_charge_rate_kw` measures SOC gained per window hour, so the learned rate already embeds losses. Applying an efficiency on top of it would double-count.

**Design.**

- `planning.charge_efficiency(config_rate, effective_rate)` returns `CHARGE_EFFICIENCY` (0.92) when the nameplate rate is used, and 1.0 when the learned rate is used. It uses the same "more than 10% lower" test as `resolve_used_charge_rate`.
- The window is `flux1_end_minutes(energy_needed / efficiency, used_charge_rate)`, still clamped 02:15–05:00. The low-solar 05:00 window is unchanged.
- In-window house load is served from the grid in parallel with the battery charge, so it does not lengthen the window. Charge-end to 06:00 drain is already covered by the drain adjustment. `planning.window_grid_kwh` adds the load to the grid kWh instead: `grid_kwh_needed = energy_needed / efficiency + load_kw × window_hours`, where the load is phase 12's resolved `avg_consumption_kw`.

**Logged.** The plan carries `charge_efficiency`, `energy_needed_kwh`, `window_load_kwh` and `grid_kwh_needed`. `charge_efficiency`, `window_load_kwh` and `grid_kwh_needed` are in `_IMPORT_PLAN_FIELDS`, so the JSONL record keeps them for the phase 14 KPIs.

**Verification.** `tests/test_planning.py` checks that efficiency 1.0 with zero load reproduces the old minutes, that losses lengthen the window (2.9 kWh at 3 kW goes from 03:00 to 03:15), that the clamp holds, that the learned-rate path uses 1.0, and the grid kWh arithmetic. In HA, the Test plan button shows the new fields.


## Day-rate import feedback (09/10/2026, phase 15)

**Problem.** The evening SOC nudge uses fixed 35%/20% thresholds at 22:00. They were set for 80–85% targets and are a weak proxy for cost (see logic.md §8).

**Design.** `planning.import_feedback_adjustment(paired_days, today, band, away)` uses the phase 14 KPIs.

- It looks at the last 14 days in tonight's band and regime. It excludes full-charge days, export-disabled days, nights already at a 100% target, and days with no `grid_import_morning_kwh`. It needs at least 5 days.
- If the median 05:00–16:00 import is above 0.3 kWh, the result is +5.
- Otherwise, if the median `unused_charge_kwh` is above 0.5 kWh, the result is −5.
- Otherwise the result is 0.
- It returns `(adjustment, days, reason)` with reason `day_rate_import` / `unused_charge` / `on_target` / `insufficient_days`, or `target_full` when the plan is already at 100%.
- `day_kpis` gains `grid_import_morning_kwh` (05:00–16:00) for this.

**Shadow.** The plan logs `import_feedback_adjustment`, `import_feedback_days`, `import_feedback_reason` and `import_feedback_live`. The first, third and fourth are in `_IMPORT_PLAN_FIELDS`. The 22:00 bundle plan line shows them.

- The new option `import_feedback_live` defaults off. When it is on, the feedback replaces the evening SOC nudge in `apply_soc_adjustments`. The drain adjustment is unchanged.
- Turn it on once a week or two of KPIs exists and the backtest agrees.

**Verification.** `tests/test_kpis.py` covers import raising the target, unused charge lowering it, on-target, the minimum days, each exclusion, and the away regime.
