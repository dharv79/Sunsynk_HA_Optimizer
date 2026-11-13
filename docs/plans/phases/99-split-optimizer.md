# Phase 99 — Refactor 3: split optimizer.py

**Status:** Built, merged (feb06b3, PR #208). **Size:** L, ~120-175k est. **Origin:** code review 11/11/2026 (refactor). After phases 88, 90, 91 and 98 (so the fixes are not redone in two layouts).

## Goal

`optimizer.py` is 4,688 lines: one class with 177 methods. `async_run_import_plan` alone is 355 lines and `async_run_flux2_check` 161. Split by responsibility. No behaviour change.

## Design

- Keep one `SunsynkOptimizer` object (entities and services hold a reference to it), built from mixins in separate modules:
  - `optimizer.py`: construction, listener registration, `_guarded`, config cache, notify, API push helpers.
  - `opt_overnight.py`: 01:55 plan, retry, watchdog, gentle charging, charge outcome.
  - `opt_daytime.py`: SOC listener, Flux 2 check, trim, peak cover, peak export and re-checks.
  - `opt_capture.py`: 06:00 and 22:00 captures, meter snapshots, KPIs, daily cost, debug bundle.
  - `opt_reports.py`: weekly summary, AI insight, monthly reports, bill reconciliation, advisors.
  - `opt_monitor.py`: drift check, data audit, outage, PV strings, event feed, battery health.
  - `opt_events.py`: free events, flex events.
  - `opt_shadow.py`: MPC, dynamic tariff, native TOU, multi-day outlook.
- Break `async_run_import_plan` into: gather inputs → decide (already pure in `planning`) → push → log and notify. The dry run then skips the last two steps by structure, not by scattered `if dry_run` checks.
- Do-not-break rules 4, 5, 6, 7, 16 and 17 each get a structural test where one does not exist (for example: no module other than the push helper calls the API client).
- One mixin per PR. Characterisation test before each move: same plan dict and same sequence of pushes for a fixed set of inputs.
- Update `CLAUDE.md` file map and `code-map.md`.

## Risk

The largest change in the plan to a live battery controller. Ship as a beta, run a week in monitor mode beside the previous release, compare the debug stream.

## Saving

None in £. Lower token cost and risk for every later phase.

## Built (12/11/2026)

- Done as one mechanical move rather than one PR per mixin: an AST script moved every class member verbatim (all 200 are AST-identical to the original), so the diff is a relocation, not a rewrite. Mixins: `opt_overnight` (40 methods), `opt_daytime` (35), `opt_capture` (22), `opt_events` (23), `opt_monitor` (16), `opt_shadow` (13), `opt_reports` (12); `optimizer.py` keeps construction, config cache, notify, push helpers and `_guarded` (28). `opt_common.py` holds the small shared helpers/constants; mixins import only `opt_common`, never each other or `optimizer.py`.
- `tests/test_code_integrity.py` and `tests/test_state_update_skip.py` read the merged source of all files. New tests: API write calls only in `_async_post_with_status` / `_async_write_setting` (rule 5); mixins import no sibling optimizer module.
- Deferred: breaking `async_run_import_plan` into gather/decide/push/notify (a behaviour-adjacent rewrite; needs the characterisation test first); `opt_overnight.py` (~1,170 lines) could split further. Not run on a live HA install: ship as a beta and compare the debug stream in monitor mode.
