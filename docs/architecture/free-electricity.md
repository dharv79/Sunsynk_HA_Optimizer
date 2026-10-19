# Free electricity event

Read only when changing the free-electricity-event logic (phase 10, `docs/plans/phases/10-free-electricity-event.md`).

## Manual entry (28/09/2026)

Sell battery energy just before a known free-electricity period (Flux 2), then charge to 100% during it (Flux 1). The normal plan is held off for the event and restored afterwards. This slice covers **manual entry only** — the Octopus auto-detect trigger is still blocked on a live attribute sample from the user's HA (`greener_nights` calendar entity).

**Entry point:** two `datetime` entities, `datetime.free_event_start` / `datetime.free_event_end` (`datetime.py`). Setting both (end after start, start in the future) calls `SunsynkOptimizer.async_try_schedule_manual_free_event`, which delegates to `async_schedule_free_event(free_start, free_end, source="manual")`.

**Planning:** `planning.plan_free_event` (HA-free, unit-tested) computes the sell floor, sell window and expected export/refill kWh from current SOC, `CONF_BATTERY_CAPACITY`, and `CONF_FREE_EVENT_CHARGE_RATE_KW` / `CONF_FREE_EVENT_EXPORT_RATE_KW` (both default to `CONF_CHARGE_RATE` when unset).

**State:** `OptimizerState.free_event` (source, times, floor, expected kWh, `phase`: `scheduled` → `selling` (skipped if SOC is already at/below the floor) → `charging` → `done`/`cancelled`). Persisted, so a restart mid-event resumes correctly via `_arm_free_event_timers`, called both right after scheduling and from `async_setup` on startup — each callback re-arms with a 0-second delay if its trigger time already passed.

**Execution (`optimizer.py`):** three point-in-time `async_call_later` callbacks (sell start, free start, free end), each pushed via `async_push_flux_override` → `_async_post_with_status` (do-not-break rule 5):
- sell start: Flux 2 = now→free_start, `targetSoc` = floor.
- free start: Flux 1 = now→free_end, `targetSoc` = 100.
- free end: `_async_restore_normal_plan` re-pushes the latest logged Flux 1 (`last_import_plan.payload.flux_1`) plus the standard 16:00–16:15 Flux 2 slot, then clears the manual entities.

**Guard** (holding narrowed on 16/10/2026, see below): `_free_event_active()` (`phase` in scheduled/selling/charging) pauses the 30-minute Flux 2 check, the SOC-change listener, and the 60 s post-setup reload one-shot — do-not-break rule 7. The 01:55 plan still computes and logs (`held_by_free_event` field on the plan record) so it's ready to push the moment the event ends, but the push itself is skipped while held.

**Cancel:** the `button.cancel_free_event` entity → `async_cancel_free_event`. Restores the normal plan only if the event had already taken a slot (`selling`/`charging`); a still-`scheduled` cancel has nothing to restore.

**Logging:** `data_logger.async_log_free_event` appends a `free_event` record at `scheduled` / `done` / `cancelled`. Not yet read back by any adaptive learning — tag only, per the phase decisions; filter it out of drain/nudge history later if it turns out to skew results.

**Not yet built:** Octopus auto-detect (blocked), do-not-break rule additions beyond rule 7, `testing.md` row (no new HA-free pure logic was added — `plan_free_event` itself was already tested in phase 10's first slice).

## Validation and startup fixes (29/09/2026)

- **Manual entry warnings:** the start/end entities are set one at a time, so validating the pair on every set warned against a stale counterpart (four warnings in a row in `#homenotifications`). `planning.manual_free_event_error(changed, ...)` now judges only the field just set: a past start warns; a start at/after a stale end waits silently; an end at/before start warns.
- **Startup "plan skipped" warning:** the 60 s post-setup one-shot (`_async_initial_refresh`) ran before SolarSynkV3 had polled, so the SOC entity was unavailable and `_async_skip_plan` notified. It now defers (up to 5 x 60 s, `_INITIAL_REFRESH_MAX_RETRIES`) while SOC is unavailable, then falls through to the normal skip/notify.

## Octopus Saving Sessions (09/10/2026, phase 21) — removed

**Removed (16/10/2026).** Saving sessions are no longer handled. The design exported the battery down to a floor for "reward + export" per kWh. But the reward is for importing less than usual, not for exporting, so that just emptied the battery to the grid. The battery's normal behaviour (running the house) already avoids import, so the feature was removed rather than redesigned. Removed: the planner, timers and state fields, the `datetime.saving_session_*`, `button.cancel_saving_session` and `sensor.saving_session` entities, the dashboard card, the `saving_session_reward_pence` / `octopus_saving_session_entity` options, the 01:55 boost, and the peak-export/trim skips. Old registry entries are removed at setup (`__init__._remove_stale_entities`, `const.REMOVED_ENTITY_KEYS`). A stored `saving_session` state key is ignored on restore. Logged `saving_session` records stay in the JSONL history and are not read back.

## Debug-channel reports and results (16/10/2026)

**Design.** Each free event lifecycle point (`scheduled`, `done`, `cancelled`) sends one JSON line to `CONF_DATA_REPORT_TARGET` (Slack `#sunsynkdebug`) via `_async_report_event`. A blank target means nothing is sent. The line is `planning.event_report`: the stored dict plus `type` and `phase`, without the raw `meters_*` readings.

**Results.** The SolarSynk daily meters (`_read_daily_meters`) are stored in the event dict at the sell start and the free start, then differenced at the end with `planning.meter_delta`. A missing reading, or a midnight reset that makes the difference negative, gives `None`. `free_event_result` gives:
- `sold_kwh`: grid export from the sell start to the free start.
- `free_import_kwh`: grid import during the free period, valued in `free_import_value_gbp` at the normal `charges` import price for that window.
- `soc_at_end`: the SOC when the event ends.

The results go into the stored dict (and so the `done` log record) and into the "finished" notification. The sensor attributes hide the `meters_*` keys. Tests: `test_meter_delta`, `test_free_event_result_and_report`.

## Sell start decided live on the day (16/10/2026)

**Root cause.** An event entered on 16/10 for 18/10 11:00–13:00 planned a 10:28 sell start: 0.8 kWh, from the 48% SOC at scheduling time. On the day, the battery would be fuller after the overnight charge and morning solar, so it would start the free period well above the 40% floor and take only part of the 6 kWh it could import free. Holding the slots from `scheduled` also froze the 01:55 plans for the two nights in between. 1.0.12b17 re-planned once, 4.5 h ahead, from the solar forecast. 1.0.12b18 replaces that with live measurement, at the user's request: free power lasts at most 2 h, so it watches the battery for the 4 h before.

**Design.**
- **Watch:** from `planning.free_event_watch_from` (free start − `FREE_EVENT_MAX_SELL_HOURS`, 4 h), `_async_do_free_event_watch` runs every `FREE_EVENT_WATCH_MINUTES` (10) while the event is `scheduled`. If the watch time is already past at scheduling or restart, it starts at once.
  - Each check samples the SOC. `planning.soc_trend_kw` turns the last `FREE_EVENT_TREND_MINUTES` (30) of samples into the battery's real charge rate: solar going in, net of house load. It needs at least 5 minutes of samples. The samples are unpersisted, so they restart after an HA restart.
  - `planning.free_event_sell_decision` projects the SOC at the free start (a rising trend only, capped at 100%). It then works out the hours needed to export down to the floor at the export rate, and returns `sell_now` once those hours + `FREE_EVENT_SELL_MARGIN_HOURS` (15 min) reach the time left.
  - On `sell_now` it stores `sell_start` (now), `expected_export_kwh` and `sell_trigger` (the decision inputs), then runs the existing sell start: Flux 2 now→free start at the floor. It notifies and sends a `selling` debug line.
- **Estimate at scheduling:** `plan_free_event` still gives the floor and refill kWh. The sell window is capped at 4 h instead of the free period's length, which re-opens the phase 10 "may be revisited" decision. Its sell start and export kWh are no longer stored (see "Sell status on the dashboard" below).
- **Slots held from the sell start:** `_slots_held_by()` covers `selling`/`charging` only (the phase 10 decision: "holds both slots from sell start to free end"). While only `scheduled`, the 01:55 plan, Flux 2 checks and trims run normally. Scheduling still refuses a second event (`_free_event_active`).
- **01:55 plan on the day:** `planning.free_event_target_cap` caps the target when a scheduled event starts that day before 16:00. Anything above the cap would only be sold back at a lower export price. The plan record has `free_event_capped`. The cap was the bare sell floor until 1.0.12b20 (see "Overnight cap covers the morning load" below).

**Verification.** `tests/test_planning.py`:
- `test_soc_trend_kw`;
- `test_free_event_sell_decision_18_oct` (the 18/10 cases);
- `test_free_event_watch_from` and `test_free_event_target_cap`;
- `test_free_event_sell_window_not_capped_at_free_period_length` and `test_free_event_sell_window_capped_at_max_sell_hours`.

## Sell status on the dashboard (16/10/2026)

**Root cause.** The event entered at 08:01 on 16/10 for 18/10 11:00 showed "Sell start 11:00, Sell skipped true, expected export 0 kWh". That was `plan_free_event` run on the 40% SOC at scheduling time. Since 1.0.12b18 the real sell start is decided live from the watch, so the estimate only misled.

**Design (1.0.12b19).**
- Scheduling stores `watch_from` and no longer stores `sell_start`, `sell_end`, `skip_sell` or `expected_export_kwh`. The watch sets `sell_start` and `expected_export_kwh` when it starts a sell. The scheduled notification and debug line say "Watching from Sun 07:00" instead of an estimate.
- `planning.free_event_attributes` builds the `sensor.free_event` attributes: no `meters_*` keys, and no `FREE_EVENT_ESTIMATE_KEYS` until a sell has run (`free_event_sold`: `soc_at_sell_start` is present). Events stored by older versions are hidden the same way.
- It adds `sell_status` (`planning.free_event_sell_status`): "Watching from Sun 07:00", "Selling since 08:40", "Sold from 08:40", "Not needed" or "Cancelled". The dashboard Status card shows it as **Sell**, replacing the "Sell start" and "Sell skipped" rows.
- The "finished" notification reads `expected_export_kwh` with a 0 default, since an event that never sold has none.

**Verification.** `tests/test_planning.py::test_free_event_sell_status_and_attributes` (the 16/10 event).

## Overnight cap covers the morning load (18/10/2026)

**Root cause.** On 18/10 (free 11:00–13:00, floor 40%) the 01:55 plan was capped at the bare 40% floor. The battery charged 25→40% by about 02:40, ran the house at about 0.4 kW, and hit its 25% minimum at about 06:50. The house then imported at the day rate until the free start. The floor is the level wanted *at the free start*, not at the end of the cheap window.

**Design (1.0.12b20).** `free_event_target_cap(..., load_kw, capacity_kwh)` caps at the floor plus the house load from `CHEAP_WINDOW_END` (05:00) to the free start: `floor + ceil(load_kw × hours / capacity × 100)`, at most 100 and never above the normal target. The 01:55 plan passes `avg_consumption_kw` (the regime/learned load) and the battery capacity. Morning solar gets no credit: an overshoot is sold by the live watch at the export price, while a shortfall costs the day rate. Without a load figure the cap stays the bare floor. For 18/10: 40 + 0.45 kW × 6 h / 10 kWh = 67%.

**Verification.** `tests/test_planning.py::test_free_event_target_cap_covers_load_until_free_start` (the 18/10 case).

## First live event results (18/10/2026)

**Result.** Free 11:00–13:00, floor 40%. The battery reached the free start at 39% (no sell needed) and was at 100% by 13:00. Free import was 8.1 kWh (about 6.1 kWh into the battery, the rest house load), worth £2.25 at the day rate.

**Fix (1.0.12b21).** The `done` line reported `sold_kwh: null` when no sell ran, and kept the stale scheduling estimate (`sell_start` 11:00, `skip_sell` true) of an event stored before 1.0.12b19. `free_event_result` now gives `sold_kwh` 0.0 when no sell ran (`free_event_sold`); null stays for a missing meter reading. `event_report` hides `FREE_EVENT_ESTIMATE_KEYS` unless a sell ran, as the sensor already did. Test: `tests/test_kpis.py::test_free_event_result_no_sell`.

## Free import left out of the day KPIs (18/10/2026)

**Root cause.** The 18/10 `day_kpis` priced the 8.1 kWh free import at the day rate: morning import 10.7 kWh, avoidable £3.18, battery saving −£0.59. That import cost nothing. The morning figure also feeds the import-feedback nudge (phase 15), where it would read as an overnight shortfall.

**Design (1.0.12b22).** `planning.free_event_kpi_credit(event, day, capacity)` takes a finished (`done`) event on the KPI day and splits its `free_import_kwh` across the KPI segments by time overlap. It also splits the house load: import minus the battery gain from `soc_at_free_start` to `soc_at_end`. The 22:00 capture passes this to:
- `day_kpis`, which takes the import out of its band (and so out of the day, morning and avoidable £ figures) and logs `free_import_excluded_kwh`;
- `no_battery_baseline`, which leaves the import out of the actual cost and the free house load out of the no-battery cost.

Records already logged (18/10) stay as written, because they dedup at write. The real bill comes from the Octopus `daily_cost` and was never affected.

**Verification.** `tests/test_kpis.py`: `test_free_event_kpi_credit`, `test_day_kpis_leave_out_free_import`, `test_no_battery_baseline_free_import_costs_nothing`.

## Dashboard attributes always present (19/10/2026)

**Root cause.** From 1.0.12b19 `expected_export_kwh` was hidden until a sell ran. Once the recorder no longer held it, Spook flagged it as an unknown attribute used by the dashboard (the same class as the 1.0.12b14 bill-attribute fix).

**Design (1.0.12b23).** `planning.FREE_EVENT_ATTRIBUTE_KEYS` lists the attributes the dashboard reads. `free_event_attributes` always includes them, as None when unknown (and with no event at all). The other estimate keys (`sell_start`, `sell_end`, `skip_sell`) stay hidden until a sell runs.

**Verification.** `tests/test_dashboard.py::test_free_event_card_reads_only_always_present_attributes` checks that every `sensor.free_event` attribute on the dashboard is in the always-present list; `test_free_event_sell_status_and_attributes` covers the None defaults.
