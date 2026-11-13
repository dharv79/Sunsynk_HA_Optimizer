"""Phase 102: shiftable-load report (pure)."""
from datetime import date, timedelta

BANDS = [(120, 300, 15.0), (300, 1440, 30.0), (0, 120, 30.0)]
TODAY = date(2026, 11, 13)


def _days(n, block_days, kwh=1.5):
    out = {}
    for k in range(1, n + 1):
        day = [0.2] * 48
        if k in block_days:
            for i in (24, 25, 26):  # 12:00-13:30
                day[i] += kwh / 3
        out[(TODAY - timedelta(days=k)).isoformat()] = day
    return out


def test_recurring_block_found_and_valued(planning):
    res = planning.shiftable_loads(_days(20, set(range(1, 11))), BANDS, 15.0, TODAY)
    assert len(res["blocks"]) == 1
    b = res["blocks"][0]
    assert b["start"] == "12:00" and b["days"] == 10 and abs(b["kwh"] - 1.5) < 0.01
    # 1.5 kWh x 15p gap = 22.5p per use, 10/20 days -> 15 uses/30d -> 3.375 GBP
    assert abs(b["saving_gbp_month"] - 3.38) < 0.02
    assert b["fits"] is None and res["saving_gbp_month"] == b["saving_gbp_month"]


def test_one_off_is_ignored(planning):
    assert planning.shiftable_loads(_days(20, {3, 5}), BANDS, 15.0, TODAY)["blocks"] == []


def test_too_little_history_or_no_cheap_price(planning):
    assert planning.shiftable_loads(_days(5, {1, 2, 3, 4, 5}), BANDS, 15.0, TODAY) is None
    assert planning.shiftable_loads(_days(20, {1, 2, 3, 4, 5}), BANDS, None, TODAY) is None


def test_fit_check_and_text(planning):
    res = planning.shiftable_loads(_days(20, set(range(1, 11)), kwh=3.0), BANDS, 15.0, TODAY, headroom_kw=1.0)
    assert res["blocks"][0]["fits"] is False and res["saving_gbp_month"] == 0
    assert "may not fit" in planning.shiftable_text(res)
    assert planning.shiftable_text({"blocks": []}) is None


def test_cheap_window_and_bad_days_skipped(planning):
    days = _days(20, set(range(1, 11)))
    days["bad"] = [1.0] * 10
    for d in days.values():
        if len(d) == 48:
            d[8] += 2.0  # 04:00, inside the cheap window every day
    res = planning.shiftable_loads(days, BANDS, 15.0, TODAY)
    assert [b["start"] for b in res["blocks"]] == ["12:00"]
