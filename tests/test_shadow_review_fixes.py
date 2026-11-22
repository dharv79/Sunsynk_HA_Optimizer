"""Phase 129: opt_shadow.py fixes from the 21/11/2026 code review that aren't HA-free testable as
pure planning logic — pinned here by inspecting the optimizer's own source (same technique as
test_code_integrity.py and test_overnight_review_fixes.py)."""

from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "sunsynk_optimizer"
SHADOW_SRC = (PKG / "opt_shadow.py").read_text()


def _source(name: str) -> str:
    for fn in ast.walk(ast.parse(SHADOW_SRC)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
            return ast.unparse(fn)
    raise AssertionError(f"{name} not found")


# ---- A plain on/off flex-event entity must log once per event, not every 30-min poll ----

def test_flex_event_shadow_dedups_on_the_entity_state_change_not_the_display_time():
    src = _source("async_run_flex_event_shadow")
    # The old key was built from `start` (an on/off entity's display time is just "now", which
    # changes every poll so the dedup never matched). The key must now anchor to something that
    # only changes when the entity itself flips, i.e. `state.last_changed`.
    key_line = next(line for line in src.splitlines() if "key = " in line and "_flex_event_logged" not in line)
    assert "last_changed" in key_line
    assert "now.date().isoformat()" not in key_line and "{start}" not in key_line
    assert "self._flex_event_logged == key" in src


# ---- Dynamic-tariff saving_gbp must compare equal-length Flux vs Agile horizons ----

def test_dynamic_tariff_shadow_matches_flux_horizon_to_missing_tomorrow_agile():
    src = _source("async_run_dynamic_tariff_shadow")
    # When tomorrow's Agile vector is absent, the Flux side's hourly_tomorrow must be dropped too,
    # so mpc_slots stops both horizons at the same point (midnight tonight) instead of letting the
    # Flux side run on into tomorrow's solar while Agile's horizon is cut short.
    assert "vectors.get(1) is not None" in src
    i = src.index("tomorrow_for_slots")
    def_line = src[i:src.index("\n", i)]
    assert "hourly_tomorrow" in def_line and "vectors.get(1)" in def_line
    # Both _solve calls (flux and agile) must consume the guarded variable, not the raw hourly_tomorrow.
    solve_body = src[src.index("def _solve"):src.index("flux_plan = await")]
    assert "tomorrow_for_slots" in solve_body
    assert "hourly_tomorrow" not in solve_body
