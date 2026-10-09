# Phase 22 — Charge watchdog

**Status:** Done, merged (3e59565, PR #36). **Size:** S, ~40k actual. **Origin:** improvement list 2, 06/10/2026 (item 22).

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

## As built

- 02:20 listener `_async_charge_watchdog` → `async_run_charge_watchdog` (`_guarded`); window start is fixed at 02:00, so no per-plan scheduling. Reads tonight's `nightly_import_plan` (the 01:55 plan) and skips monitor mode, an active free event, no push (`api_ok is None`), no charge planned, or the window already ended.
- `planning.charge_progress_ok(..., target_soc)`: ok when SOC rose ≥25% of the expected rise (1% floor), grid import ≥50% of the expected charge power, or SOC is at target; `unknown` on missing inputs. Added `hhmm_to_minutes`.
- Stalled → one re-push via `async_push_flux_override`, then a 15-min `async_call_later` re-check (cancelled on shutdown), judged from the retry-time SOC over the minutes the window was still open. Still stalled → notification, worded on the re-push bool.
- `charge_watchdog` record: result ok / unknown / recovered / stalled, `retried`, `retry_api_ok`, SOC and grid figures, `first_check`; in `_DEDUP_TYPES`.
- Design note in `docs/architecture/import-plan.md` ("Charge watchdog").
