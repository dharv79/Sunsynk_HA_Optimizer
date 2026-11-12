# Phase 89 — Send the debug bundle as whole JSON lines

**Status:** Done, merged (6572656, PR #190). **Size:** XS, ~10-20k est. **Origin:** code review 11/11/2026.

## Goal

Every line in `#sunsynkdebug` must be parseable. Today the 22:00 bundle arrives cut mid-line across two or three Slack messages.

## Findings

- The integration does not split anything: the bundle is one `"\n".join(json.dumps(r) ...)` passed to a single `async_notify`. The cut is made downstream (the notify service or Slack), at roughly 4,000 characters.
- Typical bundle in 2.0.2: 11 lines, about 7,500 characters (plan ~2,600, `hourly_profile` ~1,700, `day_kpis` ~860, `peak_cover` ~700). A restart day adds a second plan line, about 10,000 characters.

## Design

- Pure `planning.pack_lines(lines, limit=3500)`: pack whole lines into as few messages as fit; a single line over the limit goes alone. Test with real-sized records.
- Send each pack with its own `async_notify`, title suffix "1/3".
- Apply to every debug-stream sender, not only 22:00.

## Saving

None direct. Makes the debug stream machine-readable again.
