# Phase 112 — Daily settings efficiency audit

**Status:** Done, merged (046eb50, PR #224), see Build notes. **Size:** S, ~25-40k est. **Origin:** efficiency review of the inverter manuals and the user's settings screenshots, 15/11/2026. Builds on phase 107.

## Goal

Read the inverter settings every day and flag any value that costs energy, not only values that changed. Phase 107 already reports changes once a week.

## Why (evidence)

The settings review on 15/11/2026 found the battery floor set higher than the battery needs (Shut Down 20 / Low Batt 25; the W5.3 datasheet allows 90% depth of discharge). That costs about 0.5 kWh of usable battery each evening, and nothing in the integration flagged it. Phase 107's weekly diff only shows a change, not a bad value, and a firmware update or installer visit can reset values without anyone noticing for up to a week.

## Design

- Move the phase 107 read from the Sunday sequence to a daily slot. Use an existing listener such as 06:20 data audit (no new listener), and keep the weekly change notification.
- Pure `planning.settings_efficiency_flags(values, expected)` returns `[{key, value, expected, why}]`. Each check compares a setting with a recommended value or range. `None` means the setting is missing or unreadable and is never flagged.
- First checks, all from the 15/11/2026 review:
  - Battery Shut Down above 15, or Low Batt above 20.
  - Grid trickle feed above 0.
  - Priority Load off.
  - Low Noise Mode on.
  - Grid charge amps below what the 3-hour window needs (`DEFAULT_CHARGE_RATE` and capacity).
  - Max Solar Power below the array's peak.
  - Work mode not Limited to Home, or Use Timer off.
  - Float charge disabled.
- The expected values go in `const.py` with options overrides. The setting key names are unverified, so the first build logs the raw keys it matched and those it did not.
- Output: a `settings_flags` debug JSON line every day, and one notification only when the set of flags changes. The daily health line (phase 100) shows the flag count.
- Read-only. It runs in monitor mode (rule 16).

## Saving

None direct. It keeps the floor change (about £15-25 a winter) and the charge settings from silently going back.

## Acceptance

- Pure logic in a `planning/` module with tests (rule 19). Missing data is `None`, never 0.
- No writes.
- Docs (`entities-dashboard.md` or `code-map.md`), the plan index and `testing.md` updated.
- py_compile, pyflakes and pytest pass.

## Build notes (15/11/2026)

- Daily check runs at the end of the 06:20 data audit as its own read (`async_run_settings_efficiency`); the Sunday change audit (phase 107) is untouched, so its weekly snapshot diff is unaffected.
- Rules find keys by name words (floors: `shutdown`, `low`+`cap`; `trickle`; `priority`; `noise`; `usetimer`). Unmatched rules are listed in the `settings_flags` debug line so real key names can be confirmed and the matchers corrected.
- Not built: Grid Amps, Max Solar Power, work mode and float checks (the first two are deliberate user choices; the last two have unknown value encodings), and options-flow overrides (defaults in `planning.EFFICIENCY_EXPECTED`, `expected` argument ready).
- Daily health (phase 100) lists the flag count and turns to "Check".

## Matchers fixed (18/11/2026, 588cc68, PR #237)

The 2.3.2 `settings_dump` was checked against the user's app screens. Trickle, Priority Load and Use Timer matched no key. They now use `zeroExportPower` (Grid Trickle Feed, 0 W), `energyMode` (Priority Load, 1) and `peakAndVallery` (Use Timer, 1). The floor and Low Noise matchers already hit `batteryShutdownCap`, `batteryLowCap` and `lowNoiseMode`. Tests use the real names.

The same dump also maps Max Solar Power to `solarMaxSellPower`, MPPT scanning to `mpptMulti`, Low Power Mode to `lowPowerMode` and PV Check to `pv1SelfCheck`/`pv2SelfCheck`. On 18/11/2026 the user set Max Solar Power 3,800 → 5,500 W (within the SYNK-3.6K's 7,000 W DC rating) and turned MPPT scanning, Low Power Mode and PV1/PV2 Check on. None of these is audited.
