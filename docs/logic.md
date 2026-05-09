# Sunsynk Optimizer — Logic Reference

Complete description of all decision logic as implemented in the current codebase. Intended as a technical reference for understanding, debugging, and extending the integration.

---

## Table of contents

1. [Scheduled triggers](#1-scheduled-triggers)
2. [Full-charge day selection](#2-full-charge-day-selection)
3. [Import plan (01:55)](#3-import-plan-0155)
4. [Flux 2 and trim logic](#4-flux-2-and-trim-logic)
5. [Data logging](#5-data-logging)
6. [Adaptive learning corrections](#6-adaptive-learning-corrections)
7. [Configuration parameters](#7-configuration-parameters)
8. [Known thresholds to revisit](#8-known-thresholds-to-revisit)

---

## 1. Scheduled triggers

| Time | Mechanism | Action |
|---|---|---|
| **01:55** daily | `async_track_time_change` | Run import plan |
| **06:00** daily | `async_track_time_change` | Capture morning SOC + PV power |
| **18:00 Sundays only** | `async_track_time_change` (gated in handler) | Choose best full-charge day |
| **22:00** daily | `async_track_time_change` | Capture evening SOC + actual solar kWh |
| **Every 30 min** | `async_track_time_interval` | Run Flux 2 check |
| **SOC state change** | `async_track_state_change_event` | Trigger trim or Flux 2 check |
| **60 s after startup** | `async_call_later` (one-shot) | Run initial import plan on every reload |

The 60-second startup delay exists so that HA notify services are registered before the first plan runs.

In **monitor mode**, all three main logic paths (`async_run_import_plan`, `async_run_flux2_check`, `async_choose_best_full_charge_day`) return immediately without making API calls.

---

## 2. Full-charge day selection

Runs every **Sunday at 18:00**. Scores Monday–Friday using the HA weather forecast and picks the highest-scoring day as the weekly full-charge day.

### Scoring formula

```
base = 100 - cloud_coverage - (rain_probability × 0.7)
```

Rain is weighted at 0.7× because partial rain days still allow meaningful generation.

### Condition adjustments

| Condition value | Adjustment |
|---|---|
| `sunny`, `clear` | +25 |
| `partlycloudy` | +10 |
| `cloudy`, `fog` | −10 |
| `rainy`, `pouring`, `lightning-rainy`, `snowy`, `snowy-rainy` | −25 |

### Temperature nudge

| Temperature | Adjustment |
|---|---|
| ≥ 18°C | +3 |
| ≤ 5°C | −3 |

### Day-of-week penalty

| Day | Penalty |
|---|---|
| Thursday | −5 |
| Friday | −15 |

Later-in-week penalty exists because filling up on Thursday or Friday leaves less of the week to use the stored energy before the next weekend.

### Result

The highest-scoring weekday is written to coordinator state (persisted) and logged as a `full_charge_day` record. A notification is sent with all five scores.

---

## 3. Import plan (01:55)

The core decision: how much to charge overnight and for how long. Runs daily at 01:55 and once 60 seconds after integration startup.

### Step 1 — Forecast correction

Reads the last 30 days of paired `import_plan` + `day_actuals` records and computes:

```
correction_factor = mean(actual_solar_kwh / forecast_solar_kwh)
                    over all days where forecast > 0.5 kWh
```

- Requires **7+ paired days** to activate; returns `1.0` (no correction) until then
- Capped at **0.5–3.0×**
- Applied: `adjusted_forecast = raw_forecast × correction_factor`

### Step 2 — Solar start time

Reads `sun.sun` HA entity → `next_rising` attribute (UTC timestamp), converts to local time, then adds the configured offset:

```
solar_start = local_sunrise + solar_start_offset_hours   (default 2.5 h)
hours_to_solar = max(0, solar_start_hour − 5.0)
```

`5.0` is the latest possible charge window end (05:00). Using 05:00 as the reference avoids a circular dependency between target SOC and charge end time.

### Step 3 — Target SOC

Evaluated in priority order:

| Priority | Condition | Target SOC | Reason key |
|---|---|---|---|
| 1 | Full-charge day | 100% | `weekly_full_charge_day` |
| 2 | Adjusted forecast < 7 kWh AND winter-like band | 100% | `low_solar_override_winter_like` |
| 3 | Adjusted forecast < 7 kWh (other bands) | 95% | `low_solar_override` |
| 4 | Normal day, `sun.sun` available | Solar bridge formula (see below) | `solar_bridge` |
| 5 | Normal day, `sun.sun` unavailable | Band fallback: 80/85/95% | `summer_like` / `shoulder` / `winter_like` |

**Solar bridge formula (priority 4):**

```
energy_to_cover = hours_to_solar × avg_consumption_kw
base_target_soc = 10% + (energy_to_cover / battery_capacity_kwh × 100)
base_target_soc = clamp(base_target_soc, 20%, 100%)
```

The 10% is a minimum reserve kept in the battery at solar start time.

**Adaptive adjustments** (applied on non-full-charge days after base target is set):

```
target_soc = clamp(base_target_soc + overnight_drain_adjustment + soc_adjustment, 20%, 100%)
```

See [Section 6](#6-adaptive-learning-corrections) for how each adjustment is derived.

### Step 4 — Charge window (flux1\_end)

| Condition | flux1\_end |
|---|---|
| Adjusted forecast < 7 kWh | `05:00` (full window — scarce solar, take all available cheap import) |
| Normal | Physics formula below |

**Physics formula:**

```
energy_needed = max(0, (target_soc − current_soc) / 100 × battery_capacity_kwh)
raw_minutes   = energy_needed / charge_rate_kw × 60
quarter_slots = ceil(raw_minutes / 15)            ← rounds up to next 15-min slot
flux1_end     = 02:00 + quarter_slots × 15 min
flux1_end     = clamp(flux1_end, 02:15, 05:00)
```

The 02:15 minimum ensures the window is never shorter than 15 minutes from the fixed 02:00 start, which is not worth an API call.

### Step 5 — API push

Pushes to the Sunsynk API via `async_push_flux_override`:

```
Flux 1: startTime=02:00, endTime=flux1_end, targetSoc=target_soc
Flux 2: startTime=16:00, endTime=16:15,    targetSoc=85   (placeholder reset)
```

### Step 6 — State and logging

Updates `coordinator.state` with the full plan dict and logs an `import_plan` record to the JSONL data store. Sends a notification with the plan summary.

---

## 4. Flux 2 and trim logic

Runs every 30 minutes and on every battery SOC state change.

### Evening export disable (16:00–19:00)

**Condition:** `now.hour` is 16–18 AND `grid_pac > export_disable_threshold`

**Action:** Push Flux 2 with `targetSoc=100%`

This prevents the inverter from exporting to the grid when the home is drawing heavily — grid power above the threshold indicates the home is consuming more than the panels produce, so keeping the battery full is more valuable than exporting.

Default threshold: **1500 W** (configurable).

### Daytime SOC trim (non-full-charge days)

**Condition:** `soc > 85%` AND 30-minute cooldown has elapsed AND it is not the full-charge day

**Action:** Push Flux 2: export window for 45 minutes, `targetSoc=82%`

The 82% target sits 3% below the 85% trigger, preventing oscillation where a trim to 85% would immediately re-trigger another trim. The 30-minute cooldown prevents the Sunsynk API from being hammered if the battery fluctuates around the threshold.

### Full-charge day trim (SOC state change)

**Condition:** SOC ≥ 99.5% AND today is the selected full-charge day

**Action:**
1. Schedule a 1-hour hold via `async_call_later(3600, _delayed_full_trim)`
2. After 1 hour, re-check SOC — if still ≥ 99.5%, push Flux 2: 60-minute export window, `targetSoc=82%`

The 1-hour hold allows the battery cells to fully condition at 100% before trimming. This only runs once per arrival at 100% (guarded by `pending_full_trim_cancel`).

---

## 5. Data logging

All records are appended as JSON lines to monthly files at:

```
{config_dir}/sunsynk_optimizer_data/YYYY-MM.jsonl
```

Files older than **13 months** are pruned on startup (one full year plus one month ensures year-over-year comparisons are always available).

### Record types

| Type | When written | Key fields |
|---|---|---|
| `import_plan` | 01:55 (and on manual trigger) | date, soc, forecast, target_soc, flux1_end, corrections applied |
| `morning_state` | 06:00 | date, morning_soc, morning_pv_power |
| `day_actuals` | 22:00 | date, evening_soc, actual_solar_kwh, evening_export_disabled |
| `full_charge_day` | Sunday 18:00 | chosen_day, scores dict |

### Record pairing

`async_load_paired_days(days=30)` joins records by date:
- `import_plan` + `day_actuals` must both exist for a date to be included
- `morning_state` is joined if available (optional — used for drain calculation)
- Multiple `import_plan` records for the same date: last one wins (dict keyed by date)

Derived field added at pairing time:
```
overnight_drain_pct = target_soc − morning_soc
```

---

## 6. Adaptive learning corrections

Three corrections are computed from paired records and applied during the import plan. All return neutral values until minimum day thresholds are met.

### 6.1 Forecast correction

| Parameter | Value |
|---|---|
| Minimum days | 7 paired days where `forecast > 0.5 kWh` |
| Formula | `mean(actual_solar / forecast_solar)` over last 30 days |
| Cap | 0.5–3.0× |
| Effect | Multiplied into raw forecast before band classification and target calculation |

Until 7 days accumulate, the factor is 1.0 and the raw forecast is used unmodified.

### 6.2 Overnight drain adjustment

| Parameter | Value |
|---|---|
| Minimum days | 5 valid morning states |
| Valid record filter | `morning_pv_power < 50 W` (solar not yet started) AND `not is_full_day` AND `overnight_drain_pct ≥ 0` |
| Formula | `mean(target_soc − morning_soc)` rounded to nearest 5% |
| Cap | 0–20% |
| Effect | Added to target SOC so the battery arrives at the solar start time at the intended level |

The 50 W PV threshold ensures the 06:00 SOC reading is pure battery drain, not contaminated by early solar generation. The 20% cap prevents overreaction to outlier nights (e.g. cold-weather standby spikes).

### 6.3 Evening SOC nudge

| Parameter | Value |
|---|---|
| Minimum days | 5 days in the same forecast band |
| Valid record filter | Same `forecast_band` AND `not is_full_day` AND `not evening_export_disabled` |
| Formula | `mean(evening_soc)` at 22:00 |
| Thresholds | HIGH = 35% (over-charged), LOW = 20% (under-charged) |
| Output | −5%, 0, or +5% |
| Effect | Added to target SOC to correct systematic over- or under-charging |

Evening export disabled days are excluded because the export disable action keeps the battery artificially full, which would corrupt the nudge signal.

---

## 7. Configuration parameters

All configurable via **Settings → Devices & Services → Sunsynk Optimizer → Configure**.

| Parameter | Key | Default | Description |
|---|---|---|---|
| Battery capacity | `battery_capacity_kwh` | 10.0 kWh | Used for physics-based window and solar bridge calculations |
| Charge rate | `charge_rate_kw` | 3.0 kW | Used to compute how long the charge window needs to be |
| Average consumption | `avg_consumption_kw` | 0.75 kW | Home load rate used in solar bridge calculation |
| Solar start offset | `solar_start_offset_hours` | 2.5 h | Hours after sunrise when solar produces enough to cover home load |
| Export disable threshold | `export_disable_threshold` | 1500 W | Grid draw above this between 16–19h disables Flux 2 export |
| Default full-charge day | `default_full_charge_day` | Wednesday | Used until the weather-based selector has run |
| Operation mode | `operation_mode` | auto | `auto` = full control, `monitor` = observe only |

---

## 8. Known thresholds to revisit

### Evening SOC nudge thresholds

`_EVENING_SOC_HIGH = 35%` and `_EVENING_SOC_LOW = 20%` were calibrated when the import target was 80–85%. With the solar bridge approach targeting 36–56%, the battery will naturally arrive at a lower evening SOC (it is used more through the day). This means:

- The 35% "too full" signal may never trigger, even if the system is slightly over-importing
- The 20% "too empty" signal may trigger on cloudy days when the solar bridge target was too optimistic

These thresholds should be reviewed after 2–3 weeks of solar bridge data has accumulated.

### Low-solar override threshold

The 7 kWh threshold that triggers the full-window / 95–100% override was chosen against the old band definitions. With forecast correction applied, an adjusted forecast of 6.9 kWh might still represent a reasonable solar day (if the site runs 2× the model). Consider whether the override should apply to the raw or adjusted forecast, and whether the threshold should be a configurable parameter.

### Solar bridge minimum floor

The 20% minimum target SOC is a pragmatic safety floor. On midsummer mornings with an early sunrise, the solar bridge formula can produce values as low as 10–15%. The 20% floor prevents the inverter from ever being commanded to a critically low state.
