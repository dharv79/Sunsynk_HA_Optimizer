# Phase 107 — Inverter settings snapshot and change audit

**Status:** Built (PR pending merge). **Size:** S, ~25-40k est. **Origin:** idea sweep 11/11/2026 (efficiency, visibility, reporting; tariff, heat pump, EV and diverter ideas excluded). Vet against the code before building.

## Goal

Notice when an inverter setting changes outside the integration (the installer, the Sunsynk app, a firmware update). The drift check (phase 31) covers only the Flux slots.

## Design

- Weekly read-only `async_read_settings`; store the snapshot; diff against the previous one.
- Ignore the keys the integration writes itself (Flux slots, the gentle-charge current).
- Report each change as "key: old → new" in the debug stream and one notification; keep the last snapshot in the diagnostics download (redacted, phase 93).
- No writes. Monitor mode safe (rule 16).

## Saving

None direct. Catches a changed charge current, low-SOC floor or work mode before it costs a week of wrong behaviour.

## Built (13/11/2026)

`planning/settings_audit.py` (`snapshot_settings`, `settings_changes`, `settings_change_text`); `async_run_settings_audit` in `opt_monitor.py`, called in the Sunday 18:00 sequence after the AI insight. It reads `async_read_settings`, stores `state.settings_snapshot` ({date, values}) and compares with the previous snapshot.

- Ignored keys: `sdBatteryCurrent` (the only setting the integration writes; Flux slots go through the income endpoint, not this one), `sn`, `id` and keys ending in update/create time or timestamp. Only scalar values are kept; numeric text that only changed format is not a change.
- The first run stores a baseline only. A failed read keeps the old snapshot. Changes go to a `settings_audit` JSONL record (written only when there are changes), a debug JSON line and one notification ("key: old → new", twelve listed, the rest counted).
- Read-only, so it also runs in monitor mode (rule 16). The snapshot is in `state`, so the diagnostics download carries it, masked by the existing key and value redaction.

Deviation: the real settings key list is unverified (no live sample), so volatile keys beyond the ones above may show as changes the first weeks; widen `AUDIT_IGNORE_*` once a real diff shows them.
