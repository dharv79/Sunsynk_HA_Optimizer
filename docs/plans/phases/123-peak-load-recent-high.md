# Phase 123: peak-load recent high with cold margin

Status: In progress

## Why

On 20/11/2026 the 14:00 peak cover asked for 46% at 16:00. The weekday estimate
is the median of 14 days' 16:00-19:00 house load with no margin (phase 60), and
light days (1.0-1.9 kWh) held it near 2 kWh while the last two weekdays used
3.0 and 6.1 kWh. On 19/11 the battery left 16:00 at 61%, hit the 20% floor about
18:00 and 2.1 kWh was bought at the peak rate.

## What

`planning.peak_load_recent_high(samples, kwh, margin, outside_temp_c)` takes the
p75 of the last 7 logged windows (any day type, at least 4) and, below 8 °C
outside, a 1.1 margin. When that beats the existing kWh x margin it replaces
both; it never lowers the estimate. Applied in `_async_peak_cover_check` after
the weekday median and the live load profile. The 22:00 `peak_cover` line keeps
the old figure as `peak_load_kwh_base` / `peak_load_source_base` /
`peak_margin_base`; the new source is `recent_high` or `recent_high_cold`.

Peak export (16:00) keeps its own p75 estimate. Peak minimum SOC (phase 122)
still applies on top.

## Verification

`tests/test_peak_cover.py`: with the 11-19 Nov days the estimate goes from
1.9 kWh to 4.35 kWh x 1.1 and the 16:00 requirement from ~39% to 70%.
