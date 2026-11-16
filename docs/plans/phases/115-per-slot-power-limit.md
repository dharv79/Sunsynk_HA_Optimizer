# Phase 115 — Per-slot power limit

**Status:** Shadow built (16/11/2026); live write not built. Keys confirmed from the 2.3.0 settings dump. **Size:** M, ~40-70k est. **Origin:** efficiency review, 15/11/2026. Builds on phase 73 and needs phase 114's write path.

## Goal

Set each timer slot's power limit (W) from the plan, not leave all six at the installer default. This shapes how fast the battery discharges or exports in each window.

## Why (evidence)

Each of the six System Mode timer slots has its own power limit, alongside its SOC and grid tick (SG04LP1 manual v.29). The integration models start, SOC and grid charge in `tou_slots_from_plan`, but not power.

Uses:
- **Evening (19:00-02:00):** cap the discharge at the learned load, so the battery lasts to 02:00 rather than covering spikes at full rate and running out early.
- **Peak (16:00-19:00):** spread the export over the window in place of the watt-trigger pause (phase 30 / shadow mode, rule 15).
- **Charge window:** a gentler grid-charge power, alongside or in place of the gentle-charge current (`sdBatteryCurrent`).

## Design

- Extend `planning.tou_slots_from_plan` with a `power_w` per slot. Pure `planning.slot_power_w(window, plan)` uses:
  - Evening: learned evening load ÷ hours, with a margin. Never below the house peak, so the house never imports for a power limit.
  - Peak: planned export kWh ÷ window hours.
  - Otherwise: the 3,600 W inverter limit (unchanged).
  - `None` when an input is missing, which means leaving the current value.
- Shadow first: add `power_w` to the `tou_shadow` record, and score the import it would have caused or avoided against the half-hourly history (phase 72 twin).
- Live behind a `slot_power_live` option, through the same write path and safeguards as phase 114: `_async_write_setting`, read-back, monitor mode, and free-event slot holding.
- First step: confirm the power key names (`sellTime1Pac`-style) from a live settings read. The phase goes On hold if they are not confirmed.

## Saving

Unproven, about £5-20 a year: less evening grid import when the battery runs out before 02:00, and smoother peak export. It needs the twin to size this before going live.

## Risk

Medium-high. A limit set below the house load makes the house import from the grid at peak. The floor on the house peak and the shadow scoring mitigate this.

## Acceptance

- Pure logic in `planning/shadow.py` (or a new module) with tests (rule 19). Missing data is `None`, never 0.
- Shadow first. Live only behind the option and monitor-safe.
- Docs, the plan index and `testing.md` updated.
- py_compile, pyflakes and pytest pass.

## On hold (15/11/2026)

The first step could not be met. The `#sunsynkdebug` stream (12 to 14 Nov) lists `cap1`-`cap6` in the `battery_floor` record, but that list is filtered to floor-like names, so no power key (`sellTime1Pac`-style) appears. There is no `settings_flags` line in the stream yet. `sensor.tou_shadow` confirms only `time`, `soc` and `grid_charge` per slot. Writing a guessed key name could change the wrong setting, so nothing is built.

**To release:** the `settings_flags` debug line (phase 112) or a raw settings read must show the six per-slot power key names. Once they do, build as designed. Phase 114's native write path is the one to reuse, with the house-peak floor on every limit.

## Built (16/11/2026): shadow only

- Keys confirmed: `sellTime1Pac` to `sellTime6Pac` (all 3600 W in the dump). `planning.tou_power_key(n)`.
- `planning.slot_power_w` / `tou_slot_powers`: slots 16:00-19:00 spread the planned peak kWh over 3 h; slots from 19:00 use the learned evening load x 1.25; others 3,600 W. Floored at the house peak (`planning.house_peak_kw`, the highest hourly-average load in the last 14 days' `hourly_profile` records, a floor not the true spike), capped at 3,600 W. Missing input is `None`.
- `tou_shadow` record now carries `power_w`, `inverter_power_w` and `house_peak_kw`. The peak export is only known at 16:00, so peak slots log `None` at plan time.
- No write, no `slot_power_live` option yet. Next: review a few days of records, then add the live write (phase 114 path, house-peak floor, read-back) and the twin scoring.
