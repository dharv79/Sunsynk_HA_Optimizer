# Phase 124: Flux 2 only pushes keep tonight's Flux 1 slot

Status: Done, merged (8cff51e, PR #243), released in 2.3.7

## Why

The 16:53 settings dump on 20/11/2026 showed the overnight slot as
02:00-04:30, while the plan pushed 02:00-05:00. A push that names only Flux 2
(here the 16:00-19:00 export pause) rebuilds Flux 1 from the stored config
baseline, and installs set up before this fix stored the old 02:00-04:30
default. Until the 01:55 plan re-pushes, the inverter holds a 30-minute shorter
cheap charge; if that push failed, the night would charge to 04:30 only.

The 4pm slot at 100% in the same dump is the export pause working as designed
(it holds target SOC at 100% so the battery does not export during the peak)
and returns to 85% when export re-enables. It is not changed.

## What

`planning.carry_plan_flux1(payload, plan)` adds the latest import plan's
`flux_1` to a push that names no Flux 1. `async_push_flux_override` applies it
to every push, after the peak-cover hold carry (phase 90), so a hold or plan
slot still wins. With no plan yet the config baseline is used as before.
`default_flux_products()` now ends Flux 1 at 05:00 for new installs.

## Verification

`tests/test_planning.py`: `test_carry_plan_flux1_keeps_tonights_slot`,
`test_default_flux1_ends_with_the_cheap_window`. Live: the next export pause or
trim settings dump shows `sellTime2` 05:00.
