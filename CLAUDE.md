# CLAUDE.md

## Maintaining this file

- This file loads in full on every turn: keep it an **index**, under ~1,000 lines. Only durable, load-bearing facts: summary, file map, do-not-break rules, conventions, how to run/test.
- Never add bug write-ups, diagnosis narratives, old-vs-new code, per-commit reasoning or feature history — those go in git history and `docs/`.
- Before adding a paragraph ask: "Does every future turn need this?" If only turns touching one area do, put it in that area's `docs/architecture/<topic>.md` and at most add a row to the topic table.
- When a feature ships, add a dated section ("Feature X (DD/MM/YYYY)") with design, root cause and verification to the relevant topic file. New test file → row in `testing.md`.
- Re-audit every few phases; move anything historical out to `docs/`.

## Project

Home Assistant custom integration (HACS) that optimises a Sunsynk inverter's overnight charging and evening export using solar forecasts, battery SOC and time-of-use (Octopus Flux) tariff windows. Pure Python, no build step: `custom_components/sunsynk_optimizer/` is deployed directly into HA. Talks to the Sunsynk cloud API; reads SolarSynkV3 and optional Octopus Energy sensors.

## File map (`custom_components/sunsynk_optimizer/`)

| File | Role |
|---|---|
| `__init__.py` | Entry setup: builds coordinator → optimizer, forwards platforms |
| `coordinator.py` | `OptimizerState` (single source of truth), `Store` persistence, `update_state`, 13-month log pruning |
| `optimizer.py` | Business logic + HA listeners (01:55, 06:00, 18:00 Sun, 22:00, 30-min, SOC change) |
| `planning.py` | HA-free planning maths (target SOC tree, charge rate, Flux 1 window, scoring, cost helpers) |
| `data_logger.py` | Monthly JSONL logging, pairing, adaptive `compute_*` corrections |
| `api.py` | Sunsynk cloud API: RSA login, token refresh, income POST |
| `flux_helpers.py` | `fluxProducts` payload, `merge_entry_data`, peak-price helper |
| `sensor.py` / `binary_sensor.py` / `button.py` / `switch.py` | Entities (event-driven, no polling) |
| `dashboard_installer.py` | Generates Lovelace YAML |
| `config_flow.py` / `const.py` | Multi-step config/options flow; constants and defaults |

## Do-not-break (reasoning: `docs/architecture/do-not-break.md`)

1. Read config only via `merge_entry_data(dict(entry.data), dict(entry.options))` — `flux_helpers.py`.
2. `plant_id` (API) and `inverter_serial` (entity IDs) are different; never swap — `api.py`, `dashboard_installer.s()`.
3. Flux index 0 = import (`direction=1`), index 1 = export (`direction=0`) — `flux_helpers.apply_flux_override`.
4. Mutate state only via `coordinator.update_state` — `coordinator.py`.
5. Push only via `_async_post_with_status`; gate notification text on its bool — `optimizer.py`.
6. Scheduled callbacks run through `_guarded` — `optimizer.py`.
7. Reload one-shot skipped 16:00–19:00 while `evening_export_disabled` — `optimizer.py`.
8. Low-solar decisions use `min(raw, corrected)` forecast — `planning.select_target_soc`.
9. Drain nights require `initial_soc < target_soc` — `data_logger._is_drain_night`.
10. Plan fields read back by `_pair_records` must be in `_IMPORT_PLAN_FIELDS` — `data_logger.py`.
11. Missing cost is `None`, never 0 — `planning.net_cost_gbp`.
12. `daily_cost` is dated per sensor and merged fill-only; never overwrite or misdate — `data_logger.merge_daily_cost_fields`, `optimizer._read_octopus_previous_day_cost`.
13. Other per-day record types dedup at write — `data_logger._write_record` / `_DEDUP_TYPES`.
14. `cost_trigger` stays `None` when no price applies, never 0p — `flux_helpers.peak_import_price_pence_per_kwh`.
15. Shadow mode defaults on (watt trigger drives export-disable) — `CONF_COST_AWARE_EXPORT_SHADOW_MODE`.
16. Monitor mode makes no API writes — `optimizer.py` early returns.
17. Test plan button is a pure dry run (no push, log or state) — `async_run_import_plan(dry_run=True)`.
18. Away days filtered by regime for drain/nudge; excluded from charge rate — `data_logger.py`.
19. New decision logic goes in `planning.py` with a test.

## Conventions

- Optional sensors degrade gracefully: blank config → `None`, never raise.
- Notification titles: `🔋 Sunsynk: <sentence case>`; failed push → "⚠️ Sunsynk: … NOT applied".
- Machine-readable JSON lines go to `CONF_DATA_REPORT_TARGET` (debug stream, Slack `#sunsynkdebug`); human text to `CONF_NOTIFY_TARGET`.
- Blocking file I/O via `hass.async_add_executor_job`.
- Transient per-window state (`_peak_window_start`, `_shadow_export_stats`) is unpersisted by design.
- Version lives in `manifest.json`; release = push `release/<version>` branch (tag containing `bN` → prerelease), via `.github/workflows/release.yml`.

## Run / test

```bash
pip install pytest && python3 -m pytest                          # HA-free unit tests
python3 -m py_compile custom_components/sunsynk_optimizer/*.py  # syntax check (CI runs both)
```
Real behaviour needs an HA instance; use the **Test plan (dry run)** button. Details: `docs/architecture/testing.md`.

## Topic docs (read only when changing that area)

| Topic | File | Covers |
|---|---|---|
| Code map | `docs/architecture/code-map.md` | Per-module responsibilities, listeners, data flow |
| Data logging | `docs/architecture/data-logging.md` | Record types, dedup, pairing, `compute_*` corrections, home/away split |
| Octopus cost | `docs/architecture/octopus-cost.md` | Octopus sensors, per-sensor dating, daily_cost merge, year-to-date |
| Import plan | `docs/architecture/import-plan.md` | 01:55 plan, target SOC selection, Flux 1 sizing, full-charge-day scoring |
| Export control | `docs/architecture/export-control.md` | 16:00–19:00 export-disable, watt vs cost trigger, shadow mode |
| Weekly reports | `docs/architecture/weekly-reports.md` | Sunday cost digest, AI Task insight |
| Entities & dashboard | `docs/architecture/entities-dashboard.md` | Sensors, buttons, binary sensors, Lovelace generator |
| Config | `docs/architecture/config.md` | Entry data/options split, ID distinction, options flow, operation modes |
| Testing | `docs/architecture/testing.md` | Test setup, HA iteration, test-file table |
| Do-not-break | `docs/architecture/do-not-break.md` | Reasoning behind each rule above |
| Logic reference | `docs/logic.md` | End-to-end user-facing logic walkthrough |
| Plans | `docs/plans/README.md` | Phase status, locked decisions, binding process; phases in `docs/plans/phases/` |

## Session and reporting rules

- Output only modified functions or blocks; no echoing code/logs/errors, no boilerplate, no intro/outro around edits.
- Don't re-derive established facts or re-open locked decisions (`docs/plans/README.md`).
- Remind the user to run `/compact` after a complex task or before a new substantive one.
- At every stop (phase merged and verified, or "what's left"): show **Remaining phases** — table `Phase | Status | Size / tokens` in index order, not-done rows only (all rows, titled **Phase status**, only when asked). Status ∈ "Planned, not built" / "In progress" / "On hold" / "Done, merged (<sha>, PR #N)" / "Superseded by …". Size bands: XS <~30k, S ~15-50k, M ~40-90k, L ~90-175k, XL 250k+; estimate for not-built, actual for done. Then: planned build order (one sentence, if any); one line of out-of-plan open items; tokens remaining in the context window; ask which phase next; remind `/compact`. Stop — don't start the next phase until told.
