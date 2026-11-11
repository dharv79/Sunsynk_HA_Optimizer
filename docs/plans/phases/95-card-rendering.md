# Phase 95 — Card rendering: redraw only on change

**Status:** Planned, not built. **Size:** S, ~20-35k est. **Origin:** code review 11/11/2026.

## Findings

- Flow card: `set hass` calls `_render()` on every state change anywhere in HA (about 25 lookups and unconditional DOM writes).
- Day chart: every pointer move rebuilds the whole SVG (five series of 288 bins) even when the cursor is on the same bin.
- Day chart: `_day` is set once; a display left on the tab stops refreshing at midnight and the Next and Today buttons stay disabled.

## Design

- Flow card: build the watched entity list in `setConfig`; return early when every watched state object is unchanged.
- Day chart: skip when the bin is unchanged; update only the cursor line, time label and legend values.
- Day chart: when following today and the date rolls, move to the new day.
- Bump the card version so browsers reload it.

## Saving

None. Lower CPU on wall tablets and phones.
