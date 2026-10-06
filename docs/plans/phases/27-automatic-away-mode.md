# Phase 27 — Automatic away mode

**Status:** Planned, not built. **Size:** XS–S, ~15-30k est. **Origin:** improvement list 2, 06/10/2026 (item 28).

## Goal

Stop forgotten manual switching from mixing holiday days into home learning (and vice versa).

## Design

- Optional config: list of `person.*` entities and/or a `calendar.*` entity + keyword (e.g. "holiday").
- Pure `planning.auto_away(persons_home: list[bool|None], calendar_away: bool|None, min_hours_away)` → `True/False/None` (None = no signal → leave the manual switch alone).
- Evaluated at 22:00 and 01:55 (before the plan). Away needs everyone away for ≥ N hours or an active calendar event; home returns as soon as anyone is home.
- Changes go through `update_state(away_mode=…)`; notify on each auto change. The manual switch still works and wins until the next auto change.

## Acceptance

- Tests for the decision (mixed presence, unavailable entities, calendar only).
- No config → identical to today.
