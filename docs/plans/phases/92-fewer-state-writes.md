# Phase 92 — Fewer state writes on SOC changes

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** code review 11/11/2026.

## Goal

Stop every battery SOC change from re-saving the whole state and refreshing every entity.

## Findings

- `_refresh_battery_runtime` stores a dict that includes the SOC, so it always differs: every coordinator entity is notified and the Store is re-saved. The saved state includes the 48-slot `mpc_plan`, `load_profile`, both plans, the battery-health ledger (up to 800 points) and `last_notification`, which holds the full 7-10 kB debug bundle text.
- Above 85% SOC on a normal day each SOC event also runs the Flux 2 check, whose "no trigger" branch writes with `touch=True`, so `updated_at` changes every time.
- `sensor.py`: `last_error`, and the `str(plan)` / `str(action)` fallbacks, can exceed HA's 255-character state limit, which turns the sensor to `unknown` exactly when an error needs showing.

## Design

- `update_state(persist=False)` for derived fields only (`battery_runtime`, `marginal_cost`, `import_causes`). Never for `free_event`, `gentle_charge`, `evening_export_disabled` or `peak_export` (rule 4 still applies: all writes go through `update_state`).
- `touch=False` on the no-trigger branch (check no dashboard card relies on it).
- Store a truncated `last_notification` (title plus the first 500 characters).
- Cap sensor states at 255 characters and put the full text in an attribute.
- Extend `test_state_update_skip.py`.

## Saving

None in £. Less SD-card wear and fewer recorder rows.
