# Phase 58 — Plain-English plan explanation on the notification and sensor

**Status:** Planned, not built. **Size:** S. **Category:** improvement. **Origin:** feature/improvement sweep, 01/11/2026 (vetted against code).

## Goal

Generate a short 'why' line from the plan's reason codes, for example: 'Target 100%: forecast 4.4 kWh (min of raw/corrected) below the low-solar line; +20 drain; not capped by a free event.' Add the soc_cap_shadow counterfactual where available. Put it in an explanation attribute on the import-plan sensor, the plan notification and the dry-run output.

## Why (evidence)

The Test plan dry run sends json.dumps(plan_state), about 30 raw fields (optimizer.py:1155-1159), and the plan notifications give only start, end and target. A grep finds no explanation helper. Users see 100% every night with no reasoning, which makes the go-live reviews and day-to-day trust harder.

## Design

Add planning.explain_plan(plan_state, shadow=None) that returns a list of sentences from the reason/band, forecast_used, the drain/nudge/feedback adjustments, the free-event cap and the full-charge-day flag. Expose it as an attribute on the import-plan sensor in sensor.py and append it to the notification body. Keep the dry-run pure (do-not-break 17): it only adds text to the dry-run notification. Add one test per reason code.

## Value

Less time decoding JSON and fewer manual dry runs, and it makes each night's decision auditable for non-technical household members.

## Risk

The text could drift from the logic. Generating it from the same fields, with tests, keeps them aligned.

## Vetting note

Confirmed that the dry run dumps raw JSON and that no explain helper exists. It is cheap, independent and adds explainability variety.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
