# Phase 116 — Time at high state of charge

**Status:** Planned, not built. **Size:** XS-S, ~20-35k est. **Origin:** user question on the battery's upper limit, 15/11/2026.

## Goal

Record how many hours a day the battery sits at 95% or above, so a decision on an upper SOC cap rests on measured data.

## Why (evidence)

The user prefers battery health and asked about an 85% cap. The `soc_cap_shadow` log (05/11-14/11/2026) shows a cap on winter low-solar nights costs about £0.13 a night, because the battery is then fully used: the cut energy is bought again at the day rate. In winter the battery is full only from about 05:00 to mid-morning.

LFP wear at high charge depends mostly on time spent full. That is likely to be long in summer (full from solar by midday until evening), but nothing measures it. The decision of 15/11/2026 was: no cap in winter, keep the weekly 100%, and review after summer with this data.

## Design

- Reuse the phase 86 day buffer (`_sample_hourly`: 30-minute SOC samples, no new listener).
- Pure `planning.high_soc_hours(samples, threshold=95)` returns hours at or above the threshold, crediting each step to the earlier sample (as `planning.hourly_profile` does). `None` when there are fewer than two samples. Also return hours at 100%.
- Add `high_soc_hours` and `full_soc_hours` to the 22:00 `day_kpis` record. The buffer only covers the time since the last restart, so also log `high_soc_sample_hours` (hours covered), and treat a partly covered day as partial, not 0.
- Add a 30-day and a year-to-date total to the battery health trend (`battery_health_trend`, phase 75) and the monthly report.
- Read-only. No writes. It runs in monitor mode (rule 16).

## Follow-up (not in this phase)

Whether a summer cap is possible at all. The timer-slot SOC limits grid charging, not solar charging, so a cap on solar charging may need a setting the integration does not control. Check this before proposing a live cap.

## Saving

None direct. It informs the cap decision (about £15-20 a winter at stake if a winter cap were applied).

## Acceptance

- Pure logic in a `planning/` module with tests (rule 19). Missing data is `None`, never 0.
- Docs (`data-logging.md`), the plan index and `testing.md` updated.
- py_compile, pyflakes and pytest pass.
