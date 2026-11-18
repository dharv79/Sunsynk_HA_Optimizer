"""Phase 112: daily inverter settings efficiency flags (pure)."""

# Real cloud key names, confirmed from the 2.3.2 settings dump against the Sunsynk app.
CLEAN = {
    "batteryShutdownCap": "15", "batteryShutdownVolt": "46", "batteryLowCap": "20", "batteryRestartCap": "30",
    "zeroExportPower": "0", "energyMode": "1", "lowNoiseMode": "0", "peakAndVallery": "1",
}


def test_clean_settings_no_flags(planning):
    flags, matched, unmatched = planning.settings_efficiency_check(CLEAN)
    assert flags == [] and unmatched == [] and len(matched) == 6


def test_real_key_names_matched(planning):
    _, matched, _ = planning.settings_efficiency_check(CLEAN)
    assert matched == {
        "shutdown": "batteryShutdownCap", "low_batt": "batteryLowCap", "trickle": "zeroExportPower",
        "priority_load": "energyMode", "low_noise": "lowNoiseMode", "use_timer": "peakAndVallery",
    }


def test_floors_and_bools_flagged(planning):
    s = {**CLEAN, "batteryShutdownCap": "20", "batteryLowCap": "25", "zeroExportPower": "50",
         "energyMode": "0", "lowNoiseMode": "1", "peakAndVallery": "false"}
    flags = planning.settings_efficiency_flags(s)
    assert [f["key"] for f in flags] == ["batteryLowCap", "batteryShutdownCap", "energyMode", "lowNoiseMode",
                                         "peakAndVallery", "zeroExportPower"]
    assert flags[0]["value"] == 25.0 and flags[0]["expected"] == "<= 20"


def test_missing_or_unreadable_never_flagged(planning):
    flags, matched, unmatched = planning.settings_efficiency_check({"batteryLowCap": "n/a", "energyMode": ""})
    assert flags == [] and "low_batt" in matched and "shutdown" in unmatched


def test_low_batt_ignores_restart_key_and_not_dict(planning):
    _, matched, _ = planning.settings_efficiency_check({"batteryRestartLowCap": "30"})
    assert "low_batt" not in matched
    assert planning.settings_efficiency_check(None) == ([], {}, [])


def test_expected_override(planning):
    assert planning.settings_efficiency_flags({"batteryLowCap": "25"}, {"max_low_batt_soc": 30}) == []


def test_health_counts_flags(planning):
    kw = dict(version="1", api_ok=True, charge_shortfall_pct=0, records_written=3, records_expected=3,
              guarded_errors={}, api_failures=0, soc_age_hours=0.1, shadow_features=[])
    assert planning.daily_health(**kw)["status"] == "OK"
    h = planning.daily_health(**kw, settings_flag_count=2)
    assert h["status"] == "Check" and "2 inverter setting flag(s)" in h["reasons"] and h["settings_flags"] == 2
