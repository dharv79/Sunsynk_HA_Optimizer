# Phase 22 — Charge watchdog

**Status:** Planned, not built. **Size:** S, ~25-45k est. **Origin:** improvement list 2, 06/10/2026 (item 22).

## Goal

Catch a night where the Flux 1 charge silently fails (cloud write lost, inverter ignored it), so a missed charge doesn't mean a full day of buying at day/peak rates.

## Design

- New listener ~20 min after the planned import window start (default 02:20), run through `_guarded`; skipped in monitor mode (do-not-break 16) and when no charge was planned (`target_soc <= initial_soc`).
- Pure `planning.charge_progress_ok(start_soc, now_soc, minutes, expected_rate_kw, capacity_kwh, grid_import_w)` → `ok | stalled | unknown` (unknown when sensors unavailable — never act on unknown).
- On `stalled`: re-push the last payload once via `_async_post_with_status` (do-not-break 5), then re-check 15 min later; if still stalled, notify "⚠️ Sunsynk: overnight charge not running" with SOC and grid figures.
- Log `{"type":"charge_watchdog", result, retried, soc_start, soc_now}`; add to `_DEDUP_TYPES` (one per night).

## Acceptance

- Tests for `charge_progress_ok` (rising SOC, flat SOC with import, flat SOC no import, missing sensors).
- At most one retry per night; no push in monitor mode or dry run.
