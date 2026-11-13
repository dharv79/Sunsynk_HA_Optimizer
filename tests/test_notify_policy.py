"""Phase 108: notification levels, quiet hours, hold/send decision and the morning brief (pure)."""


def test_levels_from_titles(planning):
    lv = planning.notification_level
    assert lv("⚠️ Sunsynk: import plan NOT applied") == "action"
    assert lv("🔋 Sunsynk: grid outage") == "action"
    assert lv("🔋 Sunsynk: overnight charge stopped short") == "action"
    assert lv("🧪 Sunsynk: test plan (dry run) — 2026-11-13") == "action"
    assert lv("🔋 Sunsynk: free event scheduled") == "action"
    assert lv("🔋 Sunsynk: weekly insight") == "report"
    assert lv("🔋 Sunsynk: tariff comparison for 2026-10") == "report"
    assert lv("🔋 Sunsynk: import plan set") == "info"


def test_quiet_hours_wrap_midnight(planning):
    q = planning.in_quiet_hours
    assert q(23 * 60, "22:00", "07:00") and q(3 * 60, "22:00", "07:00")
    assert not q(12 * 60, "22:00", "07:00") and not q(7 * 60, "22:00", "07:00")
    assert q(13 * 60, "12:00", "14:00") and not q(14 * 60, "12:00", "14:00")
    assert not q(60, "", "07:00") and not q(60, "bad", "07:00") and not q(60, "07:00", "07:00")


def test_delivery_rules(planning):
    d = planning.delivery
    assert d("info", "report", 600, "", "") == "send"  # defaults change nothing
    assert d("report", None, 600, "", "") == "send"
    assert d("report", "info", 600, "", "") == "hold"
    assert d("info", "info", 600, "", "") == "send"
    assert d("info", "action", 600, "", "") == "hold"
    assert d("action", "action", 600, "", "") == "send"
    assert d("info", "report", 23 * 60, "22:00", "07:00") == "hold"  # quiet hours
    assert d("action", "report", 23 * 60, "22:00", "07:00") == "send"


def test_release_time(planning):
    assert planning.release_time("07:30", "08:00") == "07:30"
    assert planning.release_time("", "08:00") == "08:00"
    assert planning.release_time("bad", None) is None


def test_hold_item_clips_and_caps(planning):
    held = None
    for i in range(planning.HELD_LIMIT + 5):
        held = planning.hold_item(held, f"t{i}", "x " * 300, "01:00")
    assert len(held) == planning.HELD_LIMIT and held[-1]["title"] == f"t{planning.HELD_LIMIT + 4}"
    assert len(held[0]["message"]) <= planning.HELD_TEXT_CHARS and held[0]["message"].endswith("…")


def test_morning_brief_composes_and_skips_stale(planning):
    held = [{"when": "23:10", "title": "🔋 Sunsynk: import plan set", "message": "target 80%"}]
    title, msg = planning.morning_brief(
        held, {"date": "2026-11-13", "target_soc": 80, "solar_forecast_kwh": 4.2},
        {"date": "2026-11-13", "morning_soc": 76}, {"date": "2026-11-13", "total_kwh": 1.5}, "2026-11-13",
    )
    assert title == "🔋 Sunsynk: morning brief"
    assert "charge to 80%" in msg and "76% this morning" in msg and "4.2 kWh" in msg and "1.5 kWh of spare" in msg
    assert "- 23:10 import plan set: target 80%" in msg
    _, stale = planning.morning_brief([], {"date": "2026-11-12", "target_soc": 80}, {}, {}, "2026-11-13")
    assert stale == "Nothing to report."
