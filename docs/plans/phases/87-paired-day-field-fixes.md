# Phase 87 — Paired-day field fixes (cloud cover and user override)

**Status:** Built (11/11/2026). **Size:** XS, ~15-25k est. **Origin:** code review 11/11/2026.

## Goal

Two plan fields are logged but never copied into the paired days, so the code that reads them always sees `None` (rule 10 covers the logged field list, not the paired dict).

## Findings (confirmed by running the functions)

- `cloud_cover_pct`: `planning.py` reads `d.get("cloud_cover_pct")` from paired days for the phase 82 cloud bins. `data_logger._pair_records` does not copy it, so every sample has `cloud=None`, `forecast_quantiles_cloud` equals the base quantiles and the scorecard can never leave "not yet". Phase 82 as released in 2.0.2 collects the field but cannot use it.
- `user_override`: `_is_drain_night` and the SOC nudge filter on `not d.get("user_override")`, which is always true. Nights with a manual target are learned as if the model chose them; `docs/architecture/data-logging.md` says they are excluded.

## Design

- Add both fields to the paired dict in `_pair_records`.
- New test: build an `import_plan` with every field that any paired-day reader uses and assert each one survives `_pair_records` (this is the gap that hid both bugs).
- `cloud_cover_pct` is shadow only (safe). `user_override` changes which nights feed the drain and nudge adjustments: note the before/after adjustment in the PR.

## Saving

None direct. It makes phase 82's shadow scoring real and stops manual nights skewing the learned target.

## Built (11/11/2026)

- `_pair_records` now copies `cloud_cover_pct` and `user_override`. A scan of every other `_IMPORT_PLAN_FIELDS` entry found no further paired-day readers (the scorecard reads raw plan records).
- Tests: both fields survive pairing, missing fields are `None`, an override night is not a drain sample, and a logged cloud cover reaches `forecast_ratio_samples`. The pairing tests fail without the fix.
- Effect after deploy: `forecast_quantiles_cloud` starts to differ from the base quantiles as cloud-tagged days accumulate (only days logged since 2.0.2 carry the field). Override nights drop out of the drain and nudge samples, so the learned buffer may shift slightly if overrides were used.
