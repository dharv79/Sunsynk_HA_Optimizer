"""Phase 14/129: tools/backtest.py (offline, HA-free; loaded directly from its path)."""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "backtest.py"
_spec = importlib.util.spec_from_file_location("backtest_tool", _PATH)
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


def _night(date, soc, target, morning_soc, hours_to_solar=None, avg_consumption_kw=None,
           target_soc_reason=None, away=False, charge_efficiency=None):
    plan = {
        "type": "import_plan", "date": date, "soc": soc, "target_soc": target, "away": away,
        "hours_to_solar": hours_to_solar, "avg_consumption_kw": avg_consumption_kw,
        "target_soc_reason": target_soc_reason, "charge_efficiency": charge_efficiency,
    }
    morning = {"type": "morning_state", "date": date, "morning_soc": morning_soc}
    return [plan, morning]


def test_load_kw_resize_matches_live_bridge_soc_margin_and_clamp():
    """Phase 129: --load-kw must re-run bridge_soc() (BRIDGE_LOAD_MARGIN + its own int()/30-100
    clamp) for both the logged and alternative load and apply only the difference, not a raw
    linear delta that skips the margin and bridge_soc's own rounding/clamp.
    """
    records = _night(
        "2026-11-01", soc=30.0, target=45.0, morning_soc=50.0,
        hours_to_solar=4.0, avg_consumption_kw=0.5, target_soc_reason="solar_bridge",
    )
    out = bt.replay(records, target_offset=0, load_kw=0.8, capacity_kwh=10.0)
    assert out["load_resized_nights"] == 1
    # old_bridge = bridge_soc(4*0.5*1.25, 10) = 45 (unchanged from the logged target);
    # new_bridge = bridge_soc(4*0.8*1.25, 10) = 60; alt = 45 + (60-45) = 60.
    # A raw linear delta (the old bug) would give alt = 45 + 4*(0.8-0.5)/10*100 = 57 instead.
    assert out["alternative"]["offpeak_kwh"] == pytest.approx(3.0)  # (60-30)/100*10


def test_load_kw_resize_skips_non_solar_bridge_nights():
    records = _night(
        "2026-11-01", soc=30.0, target=45.0, morning_soc=50.0,
        hours_to_solar=4.0, avg_consumption_kw=0.5, target_soc_reason="weekly_full_charge_day",
    )
    out = bt.replay(records, target_offset=0, load_kw=0.8, capacity_kwh=10.0)
    assert out["load_resized_nights"] == 0
    assert out["alternative"]["offpeak_kwh"] == out["baseline"]["offpeak_kwh"]


def test_load_kw_none_does_not_resize():
    records = _night(
        "2026-11-01", soc=30.0, target=45.0, morning_soc=50.0,
        hours_to_solar=4.0, avg_consumption_kw=0.5, target_soc_reason="solar_bridge",
    )
    out = bt.replay(records, target_offset=0, load_kw=None, capacity_kwh=10.0)
    assert out["load_resized_nights"] == 0


def test_load_records_skips_mpc_shadow_files(tmp_path):
    (tmp_path / "2026-11.jsonl").write_text('{"type": "import_plan", "date": "2026-11-01"}\n')
    (tmp_path / "mpc-2026-11.jsonl").write_text('{"type": "mpc_plan", "date": "2026-11-01"}\n')
    records = bt.load_records(str(tmp_path))
    assert len(records) == 1 and records[0]["type"] == "import_plan"


def test_skips_away_and_incomplete_nights():
    away = _night("2026-11-01", soc=30.0, target=45.0, morning_soc=50.0, away=True)
    incomplete = [
        {"type": "import_plan", "date": "2026-11-02", "soc": None, "target_soc": 45.0},
        {"type": "morning_state", "date": "2026-11-02", "morning_soc": 50.0},
    ]
    out = bt.replay(away + incomplete, target_offset=0)
    assert out["nights"] == 0 and out["skipped"] == 2
