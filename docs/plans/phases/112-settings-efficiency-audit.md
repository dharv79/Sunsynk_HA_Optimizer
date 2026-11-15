# Phase 112 — Daily settings efficiency audit

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** efficiency review of the inverter manuals and the user's settings screenshots, 15/11/2026. Builds on phase 107.

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
