# Phase 93 — Options validation and diagnostics redaction

**Status:** Planned, not built. **Size:** S, ~25-40k est. **Origin:** code review 11/11/2026.

## Goal

Reject bad times and targets when the options form is saved, and make the diagnostics download safe to attach to a public issue.

## Findings

- Charge and Flux times are free text with no HH:MM check and no start/end check. Reproduced: a start of `"16.00"` makes `kpi_prices_pence` raise `ValueError`, which then fails the evening export check, the marginal-cost tick and the KPIs on every run. The same strings go verbatim into every income POST.
- Flux targets allow 0%; the service action enforces 20-100.
- `redact_config` masks by key name only. Passing through: Octopus entity IDs (they contain the MPAN and meter serial), `auto_away_persons`, the plant ID inside `last_flux2_action.payload.id`, and the plant ID and inverter serial inside error URLs in `last_error` and `last_api_result`. `paired_days` is not redacted at all.

## Design

- Validate `^([01]\d|2[0-3]):[0-5]\d$` and start ≠ end in `_save_charge_rows` and `async_step_flux`; re-show the form with an error. Raise the Flux target minimum to 20.
- A bad value already saved only surfaces when the form is next saved; add a startup check that logs one Repairs issue for an unparseable time.
- Redaction by value: collect the sensitive values from config (plant ID, serial, digit runs in Octopus entity IDs, person and calendar entities) and replace them in every string of the output, including `paired_days`. Extend `test_security.py`.

## Saving

None. Safety and privacy.
