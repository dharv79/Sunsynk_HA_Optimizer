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

Import window (Flux 1) is physics-based: `minutes = (energy_needed_kwh / used_charge_rate) × 60`, rounded up to the next 15-minute slot, clamped 02:15–05:00. Extended to 05:00 when `low_solar_forecast_kwh < 7`. `used_charge_rate = min(config charge rate, effective_charge_rate)`, then multiplied by a battery-temperature deration factor.

All API pushes go through `_async_post_with_status()` which returns a bool. On failure the notification title switches to a "⚠️ Sunsynk: … NOT applied" variant. On a successful push the import plan clears any stale `last_error`. The plan state dict always includes `api_ok`, `source`, `forecast_fallback`, `low_solar_forecast_kwh`, and `charge_rate_from_cache` fields. Notification titles follow a `🔋 Sunsynk: <sentence case>` convention.

### Scoring logic (full-charge day)

Scores Monday–Friday from weather forecast: base score `100 - cloud_coverage - (rain_prob * 0.7)`, adjusted by condition string (+25 sunny/clear, +10 partly cloudy, −10 cloudy/fog, −25 rain/snow), temperature (±3), and day-of-week penalty (Thursday −5, Friday −15). Highest score wins.
