# Phase 31 — Inverter drift check

**Status:** Planned, not built. **Size:** S, ~30-45k est. **Origin:** improvement list 3, 14/10/2026 (item 1).

## Goal

Catch a push that silently didn't stick, or a change made in the Sunsynk app, by reading back what the inverter actually holds.

## Design

- Every 2 h (and 10 min after every push) read the plant's income/Flux settings and the inverter grid-charge current via the existing read endpoints (`api.async_read_settings`; add a Flux read if the income GET exists — feasibility check first).
- Pure `planning.flux_drift(expected, actual)` compares start/end/targetSoc per slot; expected = the last pushed payload (persist `last_pushed_flux` in state via `_async_post_with_status`).
- On drift: notify "⚠️ Sunsynk: inverter settings differ from plan" with both values, log a `flux_drift` record; no automatic re-push (shadow first). Debounce: one alert per drift episode.
- Read-only, so it runs in monitor mode too (compares against nothing pushed → skip).

## Acceptance

- Tests for `flux_drift` (equal, differing slot, missing fields → no false alarm, HH:MM vs HH:MM:SS).
- If no Flux read endpoint exists, ship the grid-charge-current check only and mark the Flux part "On hold".
