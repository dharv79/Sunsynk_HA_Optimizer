# Phase 119 — One-off settings dump

**Status:** Done, merged (46c0b04, PR #231). **Size:** XS, ~20k. **Origin:** phase 115 hold, 16/11/2026.

## Goal

Show the real inverter setting names and values once, so the per-slot power keys (phase 115) and the three unmatched phase 112 rules (trickle feed, Priority Load, Use Timer) can be confirmed.

## Design

- `planning.settings_dump(settings, identifiers)`: every scalar setting as `{key: text}`, sorted. Credential and account-like keys, `sn`, `id` and any value equal to the serial or plant id read `**REDACTED**`. Values clip at 80 characters.
- `async_run_settings_dump` (opt_monitor): 100 s after startup, once per installed version (`settings_dump_version`), sends a `settings_dump` JSON line to `CONF_DATA_REPORT_TARGET`. Read-only, so it also runs in monitor mode. No debug target means no read. A failed read retries at the next start.

## Follow-up

Read the six per-slot power keys from the line and release phase 115, then fix the phase 112 matchers.
