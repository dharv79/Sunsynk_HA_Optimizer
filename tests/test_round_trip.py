"""Phase 103: measured round trip from BMS totals, warm/cold split, AC conversion, decision shadow, scorecard line."""


def _rec(date, charge, discharge, soc, temp=20.0):
    return {"type": "battery_health", "date": date, "charge_total_kwh": charge, "discharge_total_kwh": discharge,
            "soc": soc, "cell_temp_c": temp}


def _series(planning, days, per_charge=10.0, eff=0.95, temp=20.0, start="2026-10-01"):
    """Daily records where each day charges per_charge kWh and discharges eff*per_charge, SOC flat."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [
        _rec((d0 + timedelta(days=n)).isoformat(), 1000 + n * per_charge, 900 + n * per_charge * eff, 50.0, temp)
        for n in range(days)
    ]


def test_intervals_need_consecutive_days_and_all_fields(planning):
    recs = [_rec("2026-11-01", 100, 90, 50), _rec("2026-11-02", 110, 99, 50), _rec("2026-11-04", 130, 118, 50)]
    out = planning.round_trip_intervals(recs)
    assert [i["date"] for i in out] == ["2026-11-02"]
    assert out[0]["charge_kwh"] == 10 and out[0]["discharge_kwh"] == 9
    broken = [_rec("2026-11-01", 100, 90, 50), {**_rec("2026-11-02", 110, 99, 50), "soc": None}]
    assert planning.round_trip_intervals(broken) == []
    backwards = [_rec("2026-11-01", 100, 90, 50), _rec("2026-11-02", 90, 99, 50)]
    assert planning.round_trip_intervals(backwards) == []


def test_flat_soc_gives_discharge_over_charge(planning):
    out = planning.round_trip_measured(planning.round_trip_intervals(_series(planning, 10)), 10.0)
    assert out["overall"]["days"] == 9 and out["battery_eff"] == 0.95
    assert out["ac_eff"] == round(0.95 * planning.CHARGE_EFFICIENCY * planning.DISCHARGE_EFFICIENCY, 3)


def test_soc_change_is_credited_to_the_discharge_side(planning):
    """Charged 10 kWh/day, discharged 8.5, SOC up 10 points of a 10 kWh pack (+1 kWh stored) -> (8.5+1)/10."""
    recs = [_rec(f"2026-10-{n:02d}", n * 10.0, n * 8.5, 40.0 + n * 10.0) for n in range(1, 4)]
    recs += [_rec(f"2026-10-{n:02d}", n * 10.0, n * 8.5, 70.0 - (n - 4) * 10.0) for n in range(4, 7)]
    ints = planning.round_trip_intervals(recs)
    # 5 intervals; the SOC ups and downs mostly cancel, so just check the sum is used, not the mean of ratios
    out = planning.round_trip_measured(ints, 10.0)
    stored = sum(i["delta_soc"] for i in ints) / 100 * 10.0
    assert out["overall"]["stored_kwh"] == round(stored, 2)
    assert out["battery_eff"] == round((sum(i["discharge_kwh"] for i in ints) + stored) / sum(i["charge_kwh"] for i in ints), 3)


def test_too_little_energy_reports_none(planning):
    out = planning.round_trip_measured(planning.round_trip_intervals(_series(planning, 2)), 10.0)
    assert out["overall"] is None and out["battery_eff"] is None and out["ac_eff"] is None


def test_implausible_figure_is_dropped(planning):
    ints = planning.round_trip_intervals(_series(planning, 10, eff=1.4))
    assert planning.round_trip_measured(ints, 10.0)["overall"] is None


def test_warm_and_cold_groups(planning):
    warm = _series(planning, 8, eff=0.97, temp=20.0, start="2026-10-01")
    cold = _series(planning, 8, eff=0.90, temp=4.0, start="2026-11-01")
    out = planning.round_trip_measured(planning.round_trip_intervals(warm + cold), 10.0)
    assert out["warm"]["battery_eff"] == 0.97 and out["cold"]["battery_eff"] == 0.9


def test_group_needs_enough_charge(planning):
    out = planning.round_trip_measured(planning.round_trip_intervals(_series(planning, 10)), 10.0)
    assert out["cold"] is None and out["warm"] is not None


def test_shadow_gain_under_both_figures(planning):
    # 4 kWh bought at 7p, worth 28p at peak: default 0.85 vs measured 0.80
    s = planning.round_trip_shadow(4.0, 7.0, 28.0, 0.80)
    assert s["gain_gbp_default"] == round(4 * (28 - 7 / 0.85) / 100, 2)
    assert s["gain_gbp_measured"] == round(4 * (28 - 7 / 0.80) / 100, 2)
    assert s["worth_default"] and s["worth_measured"] and s["breakeven_eff"] == 0.25


def test_shadow_flips_when_measured_is_low(planning):
    s = planning.round_trip_shadow(4.0, 20.0, 18.0, 0.95)
    assert s["worth_default"] is False and s["worth_measured"] is False
    s = planning.round_trip_shadow(4.0, 20.0, 24.0, 0.80)
    assert s["worth_default"] is True and s["worth_measured"] is False


def test_shadow_missing_input_is_none(planning):
    assert planning.round_trip_shadow(4.0, 7.0, 28.0, None) is None
    assert planning.round_trip_shadow(4.0, None, 28.0, 0.8) is None
    assert planning.round_trip_shadow(0.0, 7.0, 28.0, 0.8) is None
    assert planning.round_trip_shadow(None, 7.0, 28.0, 0.8) is None


def test_breakeven(planning):
    assert planning.round_trip_breakeven(7.0, 28.0) == 0.25
    assert planning.round_trip_breakeven(None, 28.0) is None


def test_scorecard_round_trip_line(planning):
    flip = {"worth_default": True, "worth_measured": False, "gain_gbp_default": 0.20, "gain_gbp_measured": -0.10}
    same = {"worth_default": True, "worth_measured": True, "gain_gbp_default": 0.30, "gain_gbp_measured": 0.25}
    records = [
        {"type": "peak_export", "date": "2026-11-10", "round_trip_shadow": flip},
        {"type": "peak_cover", "date": "2026-11-11", "round_trip_shadow": same},
        {"type": "peak_cover", "date": "2026-11-12", "round_trip_shadow": None},
    ]
    card = planning.shadow_scorecard(records, "2026-11-13")
    rt = card["round_trip"]
    assert rt["days"] == 2 and rt["changed"] == 1 and rt["delta_gbp"] == -0.35 and rt["evidence"] == ["2026-11-10"]


def test_digest_text_mentions_round_trip(planning):
    text = planning.digest_text({"round_trip": {"battery_eff": 0.95, "ac_eff": 0.8, "cold": {"battery_eff": 0.9}}})
    assert "95.0%" in text and "80%" in text and "90.0% on cold days" in text
