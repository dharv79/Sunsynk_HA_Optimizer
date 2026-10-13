# Weekly reports

Sunday 18:00 weekly cost summary and optional AI Task insight.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### AI Task weekly insight (v1.0.11 Part 4, optional, off by default)

A third guarded sub-call from the Sunday 18:00 listener, `_async_send_ai_weekly_insight`, gated by `CONF_ENABLE_AI_WEEKLY_INSIGHT` (default `False`). Graceful-degrade in two layers: the config toggle, then a runtime check (`hass.services.has_service("ai_task", "generate_data")` and `hass.states.async_entity_ids("ai_task")` non-empty) — an HA instance with no AI Task provider configured is a silent no-op, not an error. When both checks pass it builds a compact context string (the user's `charges` price bands, the same 7-day cost/load/solar sums as the weekly cost summary) and calls `ai_task.generate_data` with freeform `instructions` (no `structure` — the response is meant to be a short plain-English paragraph, not structured data) via `hass.services.async_call(..., blocking=True, return_response=True)`. No `entity_id` is passed, so HA routes it to the user's configured preferred AI Task entity. The generated insight is sent through `async_notify` with **no `target` override**, so it goes to the main `CONF_NOTIFY_TARGET` — deliberately separate from the JSON debug stream (`CONF_DATA_REPORT_TARGET`) the weekly cost summary and shadow-mode tally use, since a generated narrative doesn't belong alongside machine-readable lines.

## Weekly digest covers a full 7 days (26/09/2026, phase 8)

- **Symptom:** every weekly cost summary reported `days_in_period: 6`.
- **Root cause:** both weekly reports loaded `async_load_paired_days(days=7)`, which filters by `recorded_at`. At Sunday 18:00 the oldest day's 01:55 `import_plan` (~7 d 16 h old) fell outside the cutoff, and today's `day_actuals` isn't logged until 22:00, so only Mon–Sat paired.
- **Fix:** `planning.trailing_week(today)` returns the 7 complete days ending yesterday (Sun–Sat); both `_async_send_weekly_cost_summary` and `_async_send_ai_weekly_insight` load `WEEK_HISTORY_DAYS = 9` and filter with `planning.days_in_period`. The digest now carries `period_start` / `period_end`.
- **Verification:** `tests/test_planning.py` (window dates, filter, history depth). In production: next Sunday digest in `#sunsynkdebug` shows `days_in_period: 7` with the period dates.
- **Note:** a day whose Octopus cost settles after the digest (e.g. Saturday's, landing Sunday 22:00) still counts in `days_in_period` but not `days_with_cost_data`.


## Efficiency KPIs in the digest (09/10/2026, phase 14)

The `weekly_cost_summary` line also carries `planning.week_kpis` over the same trailing week: `week_grid_import_{offpeak,day,peak}_kwh`, `week_avoidable_import_gbp`, `week_export_peak_{kwh,gbp}`, `week_unused_charge_kwh` (summed; `None` when no day has the field), `week_self_sufficiency_pct` (mean over days that have it) and `week_kpi_days`. See data-logging.md.

## Monthly tariff comparison (13/10/2026, phase 28)

Options `octopus_import_consumption_sensor` / `octopus_export_consumption_sensor` point at the Octopus "previous accumulative consumption" sensors, whose `charges` attribute holds 48 half-hourly `{start, end, consumption}` kWh. `_async_capture_half_hourly` (06:00/22:00 cost capture) logs each settled day as `half_hourly_import` / `half_hourly_export` (dedup by date). Import and export settle on different days, so they are logged separately and paired by date at report time; a day that isn't 48 complete numeric slots (including DST days) is skipped, never part-logged.

On the first Sunday of a month (day ≤ 7) the 18:00 digest calls `_async_send_monthly_tariff_comparison`: `planning.compare_tariffs` costs last month's days that have both import and export under Flux (the user's configured `charges` bands), Octopus Go and Cosy (`TARIFF_IMPORT_BANDS` + `TARIFF_DEFAULT_RATES`, dated `TARIFF_RATES_AS_OF`; override via the `tariff_rate_overrides` JSON option, e.g. `{"go": {"cheap": 8.5}}`). A tariff that can't price every counted day is `None`, never a partial total. JSON line (`monthly_tariff_comparison`) goes to the debug stream, human text to the notify target.

Caveats stated in the message: usage held fixed (battery behaviour would change), unit rates only (no standing charge), rates approximate. Agile and Intelligent are not covered: they need historic half-hourly rates from the public Octopus API (follow-up).
