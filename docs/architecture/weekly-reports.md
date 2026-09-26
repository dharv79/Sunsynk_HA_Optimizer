# Weekly reports

Sunday 18:00 weekly cost summary and optional AI Task insight.

Moved verbatim from CLAUDE.md (26/09/2026). Read only when changing this area.

### AI Task weekly insight (v1.0.11 Part 4, optional, off by default)

A third guarded sub-call from the Sunday 18:00 listener, `_async_send_ai_weekly_insight`, gated by `CONF_ENABLE_AI_WEEKLY_INSIGHT` (default `False`). Graceful-degrade in two layers: the config toggle, then a runtime check (`hass.services.has_service("ai_task", "generate_data")` and `hass.states.async_entity_ids("ai_task")` non-empty) — an HA instance with no AI Task provider configured is a silent no-op, not an error. When both checks pass it builds a compact context string (the user's `charges` price bands, the same 7-day cost/load/solar sums as the weekly cost summary) and calls `ai_task.generate_data` with freeform `instructions` (no `structure` — the response is meant to be a short plain-English paragraph, not structured data) via `hass.services.async_call(..., blocking=True, return_response=True)`. No `entity_id` is passed, so HA routes it to the user's configured preferred AI Task entity. The generated insight is sent through `async_notify` with **no `target` override**, so it goes to the main `CONF_NOTIFY_TARGET` — deliberately separate from the JSON debug stream (`CONF_DATA_REPORT_TARGET`) the weekly cost summary and shadow-mode tally use, since a generated narrative doesn't belong alongside machine-readable lines.
