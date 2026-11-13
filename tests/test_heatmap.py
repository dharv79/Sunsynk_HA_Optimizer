"""Phase 106: heat-map data (pure) and the card/websocket contract."""
from datetime import date
from pathlib import Path

import pytest

TODAY = date(2026, 11, 13)
WWW = Path(__file__).parent.parent / "custom_components" / "sunsynk_optimizer" / "www"


def _imp(day, v=0.2):
    return [v] * 48


def test_import_days_oldest_first_with_gaps(planning):
    imp = {"2026-11-12": _imp(1, 0.5), "2026-11-10": _imp(1, 0.1), "2026-11-11": [1.0] * 47}  # 11th is short
    res = planning.heatmap_data("import", imp, {}, {}, TODAY, days=4, bands=[(120, 300, 15.0)])
    assert [d["date"] for d in res["days"]] == ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
    assert [d["kwh"] is not None for d in res["days"]] == [False, True, False, True]
    assert res["bands"] == [[120, 300, 15.0]] and res["kind"] == "import"


def test_scale_is_95th_percentile_not_max(planning):
    day = [0.1] * 47 + [9.0]
    imp = {"2026-11-12": day}
    res = planning.heatmap_data("import", imp, {}, {}, TODAY, days=1)
    assert res["max_kwh"] == 0.1  # one huge slot does not set the scale
    empty = planning.heatmap_data("import", {}, {}, {}, TODAY, days=3)
    assert empty["max_kwh"] == 0 and all(d["kwh"] is None for d in empty["days"])


def test_export_and_load_kinds(planning):
    exp = {"2026-11-12": [0.3] * 48}
    assert planning.heatmap_data("export", {}, exp, {}, TODAY, days=1)["days"][0]["kwh"][0] == 0.3
    # load needs snapshots; none -> None, not a crash
    assert planning.heatmap_data("load", {}, {}, {}, TODAY, days=1)["days"][0]["kwh"] is None


def test_bad_kind_and_day_limits(planning):
    with pytest.raises(ValueError):
        planning.heatmap_data("nope", {}, {}, {}, TODAY)
    assert len(planning.heatmap_data("import", {}, {}, {}, TODAY, days=500)["days"]) == planning.HEATMAP_MAX_DAYS
    assert len(planning.heatmap_data("import", {}, {}, {}, TODAY, days=0)["days"]) == 1


def test_card_calls_the_registered_websocket_command():
    js = (WWW / "sunsynk-optimizer-heatmap.js").read_text("utf-8")
    init = (WWW.parent / "__init__.py").read_text("utf-8")
    assert '"sunsynk_optimizer/heatmap"' in js and '"sunsynk_optimizer/heatmap"' in init
    assert 'const TAG = "sunsynk-optimizer-heatmap"' in js and "customElements.define(TAG" in js
    assert "?date=" in js and "_applyDateParam" in (WWW / "sunsynk-optimizer-day-chart.js").read_text("utf-8")
