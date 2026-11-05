# Phase 79 — Own Sunsynk power-flow card (replaces slipx06)

**Status:** Done, merged (1101fb0, PR #156). **Size:** L. **Category:** user request (05/11/2026, out of the idea sweep).

## Goal

Ship a modern, animated power-flow card with the integration and use it in the generated dashboard, replacing the HACS `sunsynk-power-flow-card` dependency. Decisions (user, 05/11/2026): replace the slipx06 card outright; add the optimizer extras (tonight's target SOC, import window, price now, forecast, evening export).

## Design

- `custom_components/sunsynk_optimizer/www/sunsynk-optimizer-flow-card.js`: one dependency-free custom element, `custom:sunsynk-optimizer-flow-card`. Theme-aware (HA CSS variables, light and dark), SVG hub-and-spoke diagram with animated flow lines (direction and speed follow the sign and size of each power), a self-powered ring on the hub, battery bar with tonight's-target marker and charge/discharge ETA, a "Today" energy strip and an extras strip. Built once, then patched in place on every state change so animations never restart. Responsive: stacks on phones, two columns on wide screens (container query).
- The integration serves the file (`card_resource.py`: static path plus `add_extra_js_url`, versioned query string) so no manual Lovelace resource is needed.
- `dashboard_installer._power_flow_card` emits the new card; apexcharts-card stays the only HACS card.
- Sign conventions (SolarSynkV3): `grid_pac` > 0 import, < 0 export; `battery_power` > 0 discharge, < 0 charge (`invert_battery` flips it). Missing or unavailable values render "—" and stop the flow, never 0.

## Acceptance

- Dashboard tests updated (custom cards, no unknown keys); a test that the card file exists, defines the element and registers in `window.customCards`.
- Real-HA check in Chromium at 1440 px and 390 px with seeded states.
- README, architecture doc and plan index updated; py_compile, pyflakes, pytest green.
