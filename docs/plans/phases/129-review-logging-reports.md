# Phase 129: Review fixes: logging, reports, monitor and planning maths

Status: Planned, not built

## Why

Fix the logging, report, monitoring and planning-maths findings of the 21/11/2026 code review. Each item was found by a subsystem reviewer and checked by an adversarial verifier (confirmed unless marked plausible); file:line hints are as of 3d8ae80.

## Decisions

User decision (21/11/2026): the weekly settings audit ignores the Flux timer-slot keys (`sellTimeN`, `capN`, `timeNon`; `sellTimeNPac` stays audited). Defaults chosen without a question: a failed monthly report is retried per report (the documented behaviour); weekly import-cause £ prices free-event kWh at 0 and averages the day's cost over paid kWh only.

## What

- Full-window nights are not excluded from drain learning (filter is in the evening nudge instead); the phase 62 test passes vacuously (`data_logger.py:805`, low)
- Drain nights are still gated on 06:00 PV and the 06:00 drain when the 05:00 snapshot drain is the value actually used (`data_logger.py:806`, low, plausible)
- Year-to-date and latest complete cost only count days that also have an import_plan and a day_actuals (`opt_capture.py:451`, low)
- 22:00 sub-captures are not guarded, so one exception drops last_day_actuals, later records and the debug bundle (`opt_capture.py:565`, low, plausible)
- Learned charge rate changes on 100%-target nights because the drain fallback receives 0 (`data_logger.py:897`, low)
- SOC back-calculated charge rate counts 05:00-capped windows (low-solar, gentle, full-window) at the full 3 h (`data_logger.py:970`, low)
- Forecast-fallback nights feed the forecast correction with yesterday's uncorrected forecast; forecast_fallback is not logged (`data_logger.py:829`, low)
- Unavailable PV sensors log morning_pv_power 0 W, which passes the <200 W solar-contamination gate (`opt_capture.py:376`, low, plausible)
- A truncated last line (power loss mid-write) also corrupts the next appended record (`data_logger.py:1067`, low, plausible)
- The weekly over-limit import sensor (kpi_week_over_limit_import) is always unknown (`kpis.py:37`, low)
- The scorecard's 70-day capacity window is cut to 32 days by the record load, so capacity reads 'no_records' after a 35-day gap (`opt_reports.py:567`, low)
- Monthly reports never retry: _guarded swallows each failure, then the month is marked sent (`opt_reports.py:137`, low)
- Bill check crashes with a TypeError when the bill is £0 and coverage is partial but sufficient (`opt_reports.py:641`, low)
- Bill reconciliation counts only paired days, so a logged Octopus cost on a day with no import_plan or day_actuals is dropped (`opt_reports.py:602`, medium)
- Monthly battery value loads only 40 days, so a late catch-up silently drops the start of the report month (`opt_reports.py:494`, low)
- If the Sunday digest fails, last week's digest is sent as this week's (cost summary and AI prompt) (`opt_reports.py:359`, low, plausible)
- Weekly import-cause £ uses an inflated average price and charges free-event kWh (`digest.py:55`, low, plausible)
- The export_data service builds and redacts every CSV on the event loop (`opt_reports.py:184`, low, plausible)
- Weekly settings audit reports the integration's own Flux slot changes as 'changed outside this integration' (`settings_audit.py:17`, medium)
- A failed read-back clears the drift episode, so the same drift alerts again (`opt_monitor.py:550`, low)
- Tariff price alert is cleared when the Octopus rates are merely unavailable, then re-sent (`opt_monitor.py:290`, low)
- Inverter fault flag drops a set BMS fault word whenever the workdata read fails (`opt_monitor.py:388`, low)
- 2-hour drift interval orphans the pending post-push drift check handle (`opt_monitor.py:316`, low)
- Morning-brief release can wipe a notification held while the brief was being sent (`optimizer.py:447`, low, plausible)
- Twin decides full-charge nights from full_charge_due, not the logged is_full_day (`twin.py:93`, medium)
- Monthly base-load shift flag is mostly noise in heating months (no standard-error gate) (`weather_load.py:149`, medium)
- Load-profile heating scale uses the 01:55 temperature against a daily-mean fit (`opt_overnight.py:1073`, low)
- load_report_text .capitalize() lowercases 'kWh' in the notification (`weather_load.py:176`, low)
- Shiftable-load clustering chains distinct blocks into one (`shiftable.py:116`, low)
- NaN readings get past every numeric guard and make median/percentile order-dependent (`core.py:38`, low, plausible)
- Backtest --load-kw resize omits BRIDGE_LOAD_MARGIN and bridge_soc's rounding and clamp (`backtest.py:131`, low)
- Flex event shadow logs and posts every 30 min for an on/off entity, not once per event (`opt_shadow.py:159`, low)
- Dynamic-tariff saving_gbp compares a 48-slot Flux plan with a shorter Agile plan when tomorrow's prices are missing (`opt_shadow.py:266`, low)

## Verification

New or updated HA-free tests for each fixed item: `tests/test_data_logger.py`, `tests/test_capture_review_fixes.py` (new), `tests/test_reports_review_fixes.py` (new), `tests/test_kpis.py`, `tests/test_weekly_digest.py`, `tests/test_monitor_guards.py`, `tests/test_settings_audit.py`, `tests/test_shadow_review_fixes.py` (new), `tests/test_twin.py`, `tests/test_weather_load.py`, `tests/test_overnight_review_fixes.py`, `tests/test_shiftable.py`, `tests/test_shared_helpers.py`, `tests/test_backtest.py` (new). `pytest`, `py_compile` and `pyflakes` green in CI.
