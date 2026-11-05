# Sunsynk Optimizer — How it works

A plain walkthrough of what the integration does each day, and why. Numbers are the current defaults.

---

## Contents

1. [Daily timeline](#1-daily-timeline)
2. [Modes, safety and shadow features](#2-modes-safety-and-shadow-features)
3. [Full-charge day](#3-full-charge-day)
4. [Overnight import plan (01:55)](#4-overnight-import-plan-0155)
5. [Daytime: trims and peak cover](#5-daytime-trims-and-peak-cover)
6. [Evening peak (16:00–19:00)](#6-evening-peak-16001900)
7. [Free electricity event](#7-free-electricity-event)
8. [Health checks and alerts](#8-health-checks-and-alerts)
9. [Reports and bill check](#9-reports-and-bill-check)
10. [Learning from the logs](#10-learning-from-the-logs)
11. [Entities, services and dashboard](#11-entities-services-and-dashboard)
12. [Main settings](#12-main-settings)

---

## 1. Daily timeline

| Time | What happens |
|---|---|
| **01:55** | Overnight import plan: sets the 02:00 charge window and target SOC (§4) |
| **02:00, 05:00, 16:00, 19:00, 22:00** | Meter snapshots (load, grid import/export, SOC) at the tariff band edges |
| **02:20** | Charge watchdog: if the charge is not running, re-push the plan once and re-check 15 min later |
| **05:00** | Overnight load check, charge outcome check; gentle-charge current put back |
| **06:00** | Morning SOC, PV power and overnight load; reads Octopus daily cost |
| **06:05** | Spare-solar estimate (optional notification) |
| **06:10** | Tariff price check against Octopus rates |
| **06:20** | Data audit of the last 14 days (raises HA Repairs issues) |
| **16:00** | Peak surplus export decision (§6) |
| **17:00, 18:00** | Peak export re-check from the load seen since 16:00 |
| **18:00** | Sundays: pick the week's full-charge day and send weekly reports. Every day: full-charge-day re-check and any due monthly reports |
| **22:00** | Day actuals, day KPIs, PV string check, auto-away check, Octopus cost, daily debug report |
| **22:05** | Agile vs Flux comparison (shadow) |
| **Every 30 min** | Flux 2 check (export pause and trim), peak cover, daytime import alert, cost of 1 kWh now, clipped-solar, PV-string and import-cause tallies, MPC and flex-event shadows |
| **Every 2 h**, and 10 min after each push | Drift check: reads back the inverter settings, faults and battery health |
| **On SOC change** | Trim checks, full-charge-day hold, battery runtime, cost of 1 kWh now |
| **On grid voltage change** | Grid outage start/end (§8) |
| **60 s after start or reload** | Re-runs the import plan, then peak cover and any due monthly reports. Skipped 16:00–19:00 and while a free event holds the slots. Waits for the SOC sensor if it is not ready yet |

---

## 2. Modes, safety and shadow features

**Operation mode** (`operation_mode`):

- `auto` — full control.
- `monitor` — observe only. No inverter writes (the one exception: putting back a gentle-charge current it set earlier). Read-only jobs (snapshots, alerts, reports) still run.

**Safety rules**

- Every push reports success or failure. A failed push sends "⚠️ Sunsynk: … NOT applied".
- A failed overnight plan push is retried every 10 min, up to 5 times, while it can still land between 01:30 and 02:40.
- If the SOC sensor is unavailable at 01:55, the plan retries every 5 min until 02:30, then is skipped with a warning.
- If the forecast sensor is unavailable, the plan uses yesterday's raw forecast (no correction). With no usable forecast it is skipped.
- During a grid outage, plan, trim and export pushes are paused.
- While a free event holds the Flux slots, other pushes wait (§7).

**Shadow features.** Many features first run in shadow: they log what they would do, but push nothing. Each has an option in **Configure** to make it live. All are off (shadow) by default.

| Option | When live |
|---|---|
| `import_feedback_live` | Day-rate import feedback replaces the evening SOC nudge (§10) |
| `peak_export_live` | Sells surplus 16:00–19:00 (§6) |
| `peak_cover_live` | Tops up or holds the battery before 16:00 (§5) |
| `trim_defer_live` | Trims only the solar that would overflow; the rest waits for the peak (needs `peak_export_live` too) |
| `gentle_charge_live` | Lowers the inverter's grid-charge current so the charge spreads to 05:00 |
| `full_window_live` | Flux 1 always runs to 05:00, so the grid runs the house overnight |
| `measured_charge_rate_live` | Sizes Flux 1 from the charge rate measured by the meters |
| `evening_load_live` | Evening reserve uses the learned 16:00–22:00 load |
| `load_profile_live` | Evening reserve and peak cover use the learned half-hourly load profile |
| `full_charge_skip_live` | Skips the weekly 100% if the battery reached 99% in the last 7 days |
| `cost_aware_export_shadow_mode` | **On by default.** Turn **off** to let the cost trigger drive the export pause (§6) |

Report-only shadows (never pushed): MPC plan, Agile vs Flux saving, inverter TOU differences, flex event offer, newsvendor target, learned forecast P10/P50/P90. The monthly **shadow scorecard** rates each shadow feature against what happened.

---

## 3. Full-charge day

Once a week the battery is charged to 100% for cell balancing.

**Sunday 18:00 pick.** Scores Monday–Friday from the HA weather forecast; the highest wins.

```
score = 100 − cloud_coverage − 0.7 × rain_probability
        + condition adjustment + temperature nudge − day penalty
```

| Condition | Adjustment |
|---|---|
| sunny, clear | +25 |
| partlycloudy | +10 |
| cloudy, fog | −10 |
| rainy, pouring, lightning-rainy, snowy, snowy-rainy | −25 |

Temperature: ≥ 18 °C +3, ≤ 5 °C −3. Day penalty: Thursday −5, Friday −15 (less of the week left to use the energy).

**Daily 18:00 re-check.** Moves the full-charge day to tomorrow when tomorrow's solar forecast (`tomorrow_forecast_sensor`, the lower of raw and corrected) can fill the battery from PV (capacity + 8 h of house load) **and** tomorrow's weather scores better. At most once a week, and never to a day after the chosen one.

**On the day.** If solar is good, the overnight plan only bridges to solar start and PV fills the battery for free. If solar is poor (< 7 kWh), it imports to 100%. When SOC reaches 99.5% it holds for 1 hour, then trims (§5).

**Skip (shadow).** If any meter snapshot reached 99% in the last 7 days, the forced 100% is not needed. Logged nightly; applied only with `full_charge_skip_live`. Never skipped beyond 14 days.

Until the first pick, `default_full_charge_day` (Wednesday) is used.

---

## 4. Overnight import plan (01:55)

Decides how far to charge in the cheap 02:00–05:00 window.

### Forecast

- `raw` = today's forecast (`solar_forecast_sensor`).
- `corrected` = raw × learned forecast correction (§10).
- **Low-solar decisions use the lower of the two**: if either says a poor day, believe it.
- Band: ≥ 10 kWh summer-like, ≤ 5 kWh winter-like, else shoulder.

### House load

- Weekday 0.75 kW, weekend 0.9 kW, away 0.3 kW (config).
- At home, the overnight rate learned from the 06:00 load readings replaces config once 7 days exist (last 28 days, clamped to 0.5×–2× config).

### Target SOC

Checked in order:

| Case | Target |
|---|---|
| Full-charge day, good solar | Solar bridge (PV fills to 100% in the day) |
| Full-charge day, low solar (< 7 kWh) | 100% |
| Low solar (< 7 kWh), winter-like | 100% |
| Low solar (< 7 kWh), other bands | 95% |
| Hourly forecast configured | Hourly solar bridge |
| `sun.sun` available | Simple solar bridge, raised by a morning-ramp check |
| No `sun.sun` | Band fallback: summer 80%, shoulder 85%, winter 95% |

**Solar bridge.** Enough charge to run the house from 05:00 until solar covers the load, on top of the 20% battery floor:

```
target = 20% + energy_to_bridge / capacity × 100     (clamped 30–100%)
```

- *Simple:* energy = load × hours from 05:00 to solar start (sunrise + `solar_start_offset_hours`, default 2.5 h).
- *Hourly:* walks 05:00–12:00 hour by hour, adding the load not covered by (corrected) solar, until solar covers the load.
- *Ramp check:* without an hourly sensor, a sine-shaped day built from the raw forecast is walked the same way; it can only raise the simple target.

**Adjustments** (when the target is below 100%):

```
target = clamp(base + drain buffer + evening nudge, 20%, 100%)
```

The drain buffer also keeps the expected morning SOC at or above 25%. See §10 for both.

**Caps, in order:**

1. A free event later today (before 16:00) caps the target at its sell floor plus the house load from 05:00 to the free start (§7).
2. A one-night override from `set_tonight_target` or `skip_tonight` wins over everything (§11).

### Charge window (Flux 1, 02:00 → end)

- **Low solar:** runs to 05:00.
- **Otherwise:** just long enough to reach the target.

```
energy_needed = (target − SOC) / 100 × capacity
minutes       = energy_needed / efficiency / charge_rate × 60
end           = 02:00 + minutes rounded up to 15 min, clamped 02:15–05:00
```

- **Charge rate:** the nameplate `charge_rate_kw`, unless the learned rate is more than 10% lower. Then derated for a cold battery: > 15 °C ×1.0, 10–15 °C ×0.85, 5–10 °C ×0.70, ≤ 5 °C ×0.55.
- **Efficiency:** 0.92 with the nameplate rate; 1.0 with the learned rate (losses already included).
- House load during the window adds grid kWh, not window time.
- With `full_window_live`, the window always runs to 05:00.
- With `gentle_charge_live`, the inverter's grid-charge current is lowered to the smallest that still reaches the target by 05:00 (with a 20% margin, at least 10 A, never raised), the window runs to 05:00, and the old current is restored at 05:00.

### Push

```
Flux 1: 02:00 → end, target SOC
Flux 2: 16:00 → 16:15, 85%   (standard placeholder)
```

A notification gives the plan and a short "Why". If a free event holds the slots, the plan is computed and logged, and pushed when the event ends.

**Test plan (dry run)** button: computes the plan any time and sends it as a notification. No push, no log, no state change.

---

## 5. Daytime: trims and peak cover

### Evening reserve

The SOC to keep at 19:00 so the house runs until the 02:00 cheap start:

```
reserve = ceil(20% + load × 7 h / capacity × 100)     (max 100%)
```

With 0.75 kW and 10 kWh this is 73%. `evening_load_live` or `load_profile_live` switch the load to the learned figure.

### Daytime trim (non-full-charge days)

When SOC is above 85%, sell down with a 45-minute Flux 2 window.

- Target = the highest of 82%, the evening reserve, and (before 16:00) tonight's planned target. Skipped if that is not below the current SOC.
- Only 05:00–19:00. Selling overnight and buying back later loses money.
- 30-minute cooldown between trims.
- Not while a live peak export owns Flux 2.
- **Trim defer (shadow):** before 16:00, only the solar that would overflow the battery needs trimming now; the rest can be sold at the peak rate. Live with `trim_defer_live` and `peak_export_live`.

### Full-charge-day trim

At ≥ 99.5% on the full-charge day, hold 1 hour, then (if still ≥ 99.5%) trim with a 60-minute window to the same target. Not if a live peak export, an export pause or a free event is active.

### Peak cover (shadow)

Every 30 min, 12:00–16:00: will the battery reach 16:00 with enough to run the house through the peak?

- Projects SOC at 16:00 from SOC now, forecast solar to 16:00 and today's load.
- Needs `25% + expected 16:00–19:00 load` (with margin and losses).
- If short by 3 points or more and the peak rate beats the day rate after charge losses (and wear, if set): **top up** with a Flux 1 slot now → 16:00 at the day rate, started just in time.
- From 09:00, if SOC is still above the need but falling towards it: **hold** the battery there.
- One decision logged a day. Live with `peak_cover_live`. The normal plan is put back at 16:00.

---

## 6. Evening peak (16:00–19:00)

### Export pause

Checked every 30 min and on SOC change, 16:00–19:00.

- **Watt trigger (default):** grid draw above `export_disable_threshold` (1500 W).
- **Cost trigger:** grid kW × peak import price above `export_disable_cost_threshold_pence_per_hour` (58.32 p/h). Used only when `cost_aware_export_shadow_mode` is off; otherwise just logged, and disagreements are tallied in the weekly summary.
- Action: Flux 2 16:00 → 16:15 at 100%, which stops export. Pushed once per pause, not on every check.

### Peak surplus export (shadow)

At 16:00, sell the charge not needed tonight at the peak export price.

```
target = evening reserve + expected 16:00–19:00 use + 5%
```

- Only when at least 0.5 kWh is spare and the peak export price beats the off-peak price ÷ 0.85 (plus battery wear, if set).
- Live with `peak_export_live`: Flux 2 16:00 → 19:00 at the target.
- **17:00 and 18:00 re-checks** resize the target from the load actually seen since 16:00. A push needs a move of at least 0.5 kWh.
- The export pause wins: if it fires, the battery is held at 100%.

---

## 7. Free electricity event

For free-import periods (e.g. Octopus free electricity sessions). Set by hand with the **Free event start/end** entities or the `schedule_free_event` service. **Cancel free event** stops it.

**Plan.** Sell down before the free period, then refill free.

```
sell floor = 100% − charge_rate × free hours / capacity × 100     (at least 40%)
```

Charge and export rates come from `free_event_charge_rate_kw` / `free_event_export_rate_kw` (blank = `charge_rate_kw`).

**Overnight cap.** If the event starts today before 16:00, the 01:55 target is capped at the sell floor plus the house load from 05:00 to the free start. No point buying cheap power to sell it cheaper.

**Live sell watch.** From 4 h before the free start, every 10 min:

- Projects SOC at the free start from the battery's charge trend over the last 30 min.
- Starts selling when the time to export down to the floor (+15 min) reaches the time left.
- Sell: Flux 2 now → free start, target = sell floor.

**Free period.** Flux 1 now → free end at 100%.

**End.** The normal plan is pushed back (keeping an active export pause), and a result is sent: kWh sold, kWh imported free, SOC at the end.

While selling or charging, the event holds both Flux slots: the import plan, trims, peak export and reload plan wait. Timers survive an HA restart.

---

## 8. Health checks and alerts

| Check | What it does |
|---|---|
| **Charge watchdog** (02:20) | Charge stalled → re-push once; still stalled 15 min later → alert |
| **Charge outcome** (05:00) | Alerts when the charge ended 5+ points below target, with a likely cause |
| **Overnight load** (05:00) | Alerts when 00:00–05:00 load is 1.5× the usual and 1 kWh more (home nights, 7+ nights of history) |
| **Daytime import** (05:00–16:00) | Alerts once a day when grid import stays above 1500 W (750 W away) for two 30-min checks while SOC is above 30% |
| **Grid outage** | Grid voltage < 100 V opens an outage, ≥ 180 V closes it. Notifies with battery runtime left; pauses pushes; re-runs a skipped plan when power returns |
| **Inverter drift** | Alerts once when the inverter's Flux slots or grid-charge current differ from what was pushed. Never re-pushes |
| **Inverter fault** | Reads inverter events and fault counts; new events notify and set `binary_sensor.inverter_fault` |
| **Battery health** | Reads the BMS snapshot (state of health, cells, faults); logs one record a day and tracks the trend |
| **Battery capacity** | Measures usable capacity from charge/discharge swings; alerts below 90% of the configured size |
| **PV string health** | Samples each MPPT string 10:00–15:00 (≥ 1 kW, battery not full). Alerts when one string's share has been well below usual for 3 days |
| **Clipped solar** | With `export_limit_w` set, estimates solar lost while the battery is full and export is at the limit |
| **Tariff prices** (06:10) | Alerts once when the Octopus rates no longer match the configured tariff rows |
| **Data audit** (06:20) | Missing, duplicate or contradictory records → HA Repairs issues |

`baseload_alert` (on by default) controls the overnight-load and daytime-import alerts.

---

## 9. Reports and bill check

| Report | When | Where |
|---|---|---|
| Daily data (plan, actuals, KPIs, peak, cost) | 22:00 | Debug stream (`data_report_target`) |
| Weekly cost summary | Sunday 18:00 | Debug stream |
| AI weekly insight (`enable_ai_weekly_insight`) | Sunday 18:00 | Notification |
| Shadow scorecard | First Sunday of the month | Notification and debug stream |
| Tariff comparison, battery value, upgrade advisor, capacity estimate | Once a month, at the next 18:00 or start-up after the month ends | Notification |
| Spare solar (`spare_solar_notify_kwh`) | 06:05 | Notification |

**Bill check.** Enter your latest Octopus bill's net £ in `number.billed_last_month`. It is compared with the logged Octopus costs for the latest complete bill period. The period starts on `bill_start_day` (1–28; 1 = calendar month). A gap over £2 alerts.

---

## 10. Learning from the logs

Records are JSON lines in `{config}/sunsynk_optimizer_data/YYYY-MM.jsonl`, kept for 13 months. Daily record types keep the first record per date. Days are paired by date (plan + actuals, plus morning state if present). Away days are learned separately from home days.

| Correction | Needs | How |
|---|---|---|
| **Forecast correction** | 7 days with forecast > 0.5 kWh | Median of actual ÷ forecast, capped 0.5–3.0× |
| **Drain buffer** | 5 drain nights; until then 15% (8% away) | 75th percentile of SOC lost after the charge, measured to 05:00; rounded to 5%, capped 0–20% |
| **Evening SOC nudge** | 5 days in the same band | Mean 22:00 SOC: above 35% → −5%; below 20% → +5% |
| **Day-rate import feedback** (shadow) | 5 of the last 14 days in the same band | Median 05:00–16:00 grid import > 0.3 kWh → +5%; else unused charge > 0.5 kWh → −5% |
| **Charge rate** | Calibration nights | Learned SOC gain per window hour; reused when data thins |
| **Measured charge rate** (shadow) | 3 nights | 75th percentile from the 02:00/05:00 meters, last 14 nights |
| **Overnight load** | 7 home days | Median 00:00–06:00 load, last 28 days |

Drain nights exclude: full-charge days, overrides, nights with early PV (≥ 200 W at 06:00), and nights that started at or above target. The evening nudge excludes full-charge days, export-pause days, full-window and override nights, and days with more than 15 kWh of solar.

---

## 11. Entities, services and dashboard

**Buttons:** Run choose best day, Test plan (dry run), Reset to baseline (pushes your configured Flux settings back), Update dashboard, Cancel free event.

**Switch:** Away mode. Also set automatically at 22:00 from `auto_away_persons` (away for `auto_away_min_hours`, default 24 h) or an `auto_away_calendar` event containing `auto_away_keyword` ("holiday"); the switch overrides until the next change.

**Binary sensors:** Evening export disabled, Grid outage, Inverter fault, Monitor only mode.

**Sensors** include: selected full-charge day, import plan, next import window, current SOC target, Flux 2 action, learning corrections, consumption, free event, spare solar, cost of 1 kWh now, battery runtime left, import causes, data health, PV string share, battery capacity estimate and state of health, the five day KPIs (avoidable import, peak export income, self-sufficiency, unused charge, over-limit import), and the shadow sensors.

**Services** (`sunsynk_optimizer.*`):

| Service | Does |
|---|---|
| `set_tonight_target` (`soc` 20–100) | Next 01:55 plan uses this target |
| `skip_tonight` | Next 01:55 plan holds the battery where it is (no grid charge) |
| `schedule_free_event` (`start`, `end`) | Schedules a free event |
| `cancel_free_event` | Cancels it |
| `get_plan` | Returns the dry-run plan as a response |
| `probe_inverter_events` | Read-only probe of the Sunsynk event routes |

Writes are refused in monitor mode and while a free event holds the slots.

**Dashboard.** **Update dashboard** writes the Lovelace view. It bundles its own power-flow card and day chart; it also needs the HACS **apexcharts-card**.

---

## 12. Main settings

All in **Settings → Devices & Services → Sunsynk Optimizer → Configure**.

| Setting | Key | Default |
|---|---|---|
| Battery capacity | `battery_capacity_kwh` | 10 kWh |
| Charge rate | `charge_rate_kw` | 3 kW |
| Weekday / weekend / away load | `avg_consumption_kw` / `weekend_avg_consumption_kw` / `away_avg_consumption_kw` | 0.75 / 0.9 / 0.3 kW |
| Solar start offset | `solar_start_offset_hours` | 2.5 h |
| Solar forecast (today / tomorrow) | `solar_forecast_sensor` / `tomorrow_forecast_sensor` | Forecast.Solar sensors |
| Hourly forecast | `hourly_forecast_sensor` (+ attribute) | blank |
| Export pause threshold | `export_disable_threshold` | 1500 W |
| Default full-charge day | `default_full_charge_day` | Wednesday |
| Operation mode | `operation_mode` | auto |
| Battery voltage | `battery_voltage` | 51.2 V |
| Battery cost / lifetime (wear cost) | `battery_cost_gbp` / `battery_lifetime_kwh` | blank (no wear term) |
| Export limit (clipped solar) | `export_limit_w` | 0 (off) |
| Bill period start day | `bill_start_day` | 1 |
| Octopus cost, consumption and rate entities | `octopus_*` | blank (optional) |
| Notifications / debug stream | `notify_service`, `notify_target` / `data_report_target` | `notify.notify` / blank |

Plus the live toggles in §2. Blank optional settings turn their feature off.
