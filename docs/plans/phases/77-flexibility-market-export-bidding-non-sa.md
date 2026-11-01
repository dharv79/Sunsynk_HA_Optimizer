# Phase 77 — Flexibility-market export bidding (non-Saving-Sessions VPP)

**Status:** Planned, not built. **Size:** L. **Category:** blue sky. **Origin:** idea sweep, 01/11/2026 (NOT vetted against code; estimates are the brainstormer's).

## Goal

Expose the battery's spare summer evening headroom to a third-party flexibility aggregator that pays for exporting during grid-stress events (for example Axle-style or DNO local flexibility). It is gated by the same peak-export surplus maths, so it never drains below the evening reserve.

## Why (evidence)

Saving Sessions were removed because they reward importing less, not exporting. Export-paid schemes are a different mechanism. The prior week's peak-export shadow found 8.1 kWh of real surplus (£0.67). In late October it was `no_surplus` 7 of 7 days, so the value is summer-weighted. `peak_export_plan` and `arbitrage_worth_it` already compute a safe sellable amount.

## Design

Add a `flex_events` input (a calendar or webhook entity) mirroring the free-event datetime entities. `planning.flex_event_offer(soc, reserve, peak_need, price)` reuses the `peak_export_plan` floor logic, with a test. Log a `flex_event` record of offered, delivered and paid amounts. Writes go through `_async_post_with_status` and respect `_slots_held`. Shadow first: log only what would have been offered.

## Value

Summer only: about 3–6 kWh × 20–40 events × £0.30–1.00/kWh premium is roughly £20–100 a year. The winter evidence says it is near zero from November to February.

## Risk

Scheme eligibility may clash with Octopus Flux. Delivery obligations conflict with the evening reserve. Revenue is small and only seasonal, so validate with summer `peak_export` shadow totals before building.

## Vetting note

Not yet checked against the code and plans. Run a feasibility check first and confirm no locked decision or do-not-break rule is affected.

## Acceptance

- Pure decision logic in `planning.py` with tests (rule 19); missing data is `None`, never 0.
- Ships in shadow/logged form first if it changes inverter writes; monitor mode makes no API writes.
- Docs and the plan index updated; run py_compile, pyflakes and pytest.
