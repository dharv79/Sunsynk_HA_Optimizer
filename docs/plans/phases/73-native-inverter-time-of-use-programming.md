# Phase 73 — Native inverter time-of-use programming via the six system-mode slots

**Status:** Done, shadow only (see As built). **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Program the inverter's own six `time1on`/`cap1`… system-mode slots through the settings API, not only the 2-slot Flux income POST. This allows multi-window plans: an overnight charge, a midday top-up only when needed, a peak hold and an evening floor.

## Why (evidence)

The settings dict already read by `api.py` contains the six time slots, sell mode and max sell power, and none of them are used. The 2-slot limit forces trims (up to about 28 pushes on a sunny day), the export-pause slot hack (`EXPORT_PAUSE_FLUX2_SLOT`) and the free-event slot holding. The drift read-back for Flux is undocumented. On 30–31 Oct, 5.6–6.2 kWh of day import happened with no way to schedule a cheap pre-peak top-up alongside the overnight one.

## Design

`api.async_write_settings(sn, {...})` generalising `_async_write_setting`. Add `planning.tou_slots_from_plan(plan)` mapping the plan onto 6 (start, SOC, grid-charge) tuples, with tests. Log in shadow first (`tou_shadow` record comparing it to the Flux-only plan), then a `native_tou_live` flag. Extend the drift check to read the slots back, which is a documented endpoint unlike Flux.

## Value

Fewer cloud writes (about 20 a day fewer on trim days), reliable read-back, and the MPC can be expressed without loss. Indirectly it captures the same £100+ a year the MPC targets.

## Risk

High: this writes core inverter behaviour. A wrong slot could leave the battery idle or charging at peak. It must be monitor-safe and shadow-first, and it is unverified on this inverter model.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.

## As built (05/11/2026)

- Feasibility: no locked decision or do-not-break rule touched. Nothing is written: the settings read is read-only, so rules 5 and 16 hold, and the logic is in `planning.py` (rule 19). `api.async_write_settings` already exists, so no API change was needed.
- `tou_slots_from_plan`, `tou_slots_from_settings`, `tou_diff` and `tou_settings_payload` (the last one is unused until live).
- After each 01:55 plan `async_run_tou_shadow` → `tou_shadow` record + `state.tou_shadow`.
- Not built: `native_tou_live` and the drift-check read-back of slots. Both wait for the shadow log to confirm the setting names (`readable: true`) and that the proposed slots make sense.
