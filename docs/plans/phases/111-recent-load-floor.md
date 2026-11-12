# Phase 111 — Recent-nights floor on the learned overnight load

**Status:** Done, merged (9ba6bb8, PR #192). **Size:** XS. **Origin:** 12/11/2026 short-charge night follow-up.

## Goal

Stop the solar bridge under-sizing when the weather turns cold: the 28-night median lagged (0.417 kW used; last two nights ran 0.72 and 0.52 kW).

## Design

- `planning.learned_load_kw` returns the larger of the median and the mean of the last 3 nights (at least 2 readings; same day type when the weekend split is active). Can only raise the rate.
- `resolve_load_kw` clamp (0.5x-2x config) still applies.

## Saving

Avoids day-rate morning import on bridge nights during a cold snap (roughly 15-20p a night).
