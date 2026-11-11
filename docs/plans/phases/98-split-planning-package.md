# Phase 98 — Refactor 2: split planning.py into a package

**Status:** Planned, not built. **Size:** M-L, ~70-110k est. **Origin:** code review 11/11/2026 (refactor). After phase 97.

## Goal

`planning.py` is 5,362 lines and 238 functions in one file. Split it by topic so a change loads one topic, not the whole file. No behaviour change, no import changes for callers.

## Design

- `planning/` package; `planning/__init__.py` re-exports every public name, so `from .planning import x`, the tests and `tools/backtest.py` keep working unchanged.
- Proposed modules (final cut decided from the import graph):
  - `core.py`: shared maths and time helpers, cost helpers.
  - `overnight.py`: target SOC tree, charge rate, Flux 1 sizing, gentle charging, drain and nudge feedback, full-charge day.
  - `forecast.py`: forecast correction, newsvendor, quantiles, weather bins, intraday scale.
  - `load.py`: learned loads, load profile, baseload, spikes.
  - `peak.py`: evening reserve, peak cover, peak export, trim defer.
  - `events.py`: free events, flex events.
  - `kpis.py`: meter snapshots, KPIs, import causes, scorecard, audit.
  - `reports.py`: tariff comparison, bill reconciliation, value report, upgrade and electrification advisors.
  - `battery.py`: capacity estimate, state of health, low-SOC floor, wear.
  - `shadow.py`: MPC, dynamic tariff, native TOU.
- One module per PR-sized move; `git mv`-style moves with no edits inside the functions, so each diff is reviewable.
- Guard: a test that the set of public names exported by `planning` is unchanged, plus the full suite and `pyflakes`.
- Update the file map in `CLAUDE.md` and `docs/architecture/code-map.md`.

## Saving

None in £. Lower token cost per future phase (a phase reads one ~500-line module, not 5,400 lines).
