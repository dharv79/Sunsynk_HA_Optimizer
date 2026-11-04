"""Phase 75: battery state of health from the cloud register snapshot."""

# Trimmed from a real /workdata/dynamic record (04/11/2026); pack serials replaced.
_RECORD = {
    "Sn": "SN1", "Time": "2026-11-04 16:51:43",
    "BatChargeTotal(kWh)/72-73": "4608.2", "BatDischargeTotal(kWh)/74-75": "4248.6",
    "DcTemp(℃)/90": "25.8", "WarningMessageWord1(NA)/101": "0", "FaultInformationWord1(NA)/103": "--",
    "DeyeBatBatterySoh(%)/10006": "97", "DeyeBatBatteryTemp(℃)/10008": "12",
    "DeyeBatChargeLimitCurrent(A)/10013": "128", "DeyeBatDischargeLimitCurrent(A)/10014": "200",
    "DeyeBatBatteryFault1(NA)/10019": "0",
    "Pack1Sn(NA)/10032-10039": "P1", "Pack1Soh(NA)/10048": "97.5", "Pack1CycleNumber(NA)/10056": "497",
    "Pack1TotalCapacity(Ah)/10050": "104", "Pack1MaxCell(V)/10054": "3.247", "Pack1MinCell(V)/10055": "3.244",
    "Pack1TemperateAve(℃)/10042": "12", "Pack1Fault1(NA)/10060": "0",
    "Pack2Sn(NA)/10070-10077": "P2", "Pack2Soh(NA)/10086": "97.4", "Pack2CycleNumber(NA)/10094": "508",
    "Pack2TotalCapacity(Ah)/10088": "104", "Pack2MaxCell(V)/10092": "3.246", "Pack2MinCell(V)/10093": "3.242",
    "Pack3Sn(NA)/10108-10115": "", "Pack3Soh(NA)/10124": "0",
    "PvYear(kWh)/68-69": "-8.20248576E7",
}


def test_parse_real_snapshot(planning):
    h = planning.parse_battery_health(_RECORD)
    assert h["soh_pct"] == 97.0 and h["pack_count"] == 2 and h["cycles_max"] == 508.0
    assert h["nameplate_kwh"] == 10.65 and h["health_kwh"] == 10.38
    assert h["cell_spread_mv"] == 5.0  # 3.247 max vs 3.242 min across packs
    assert h["bms_charge_limit_a"] == 128.0 and h["discharge_total_kwh"] == 4248.6
    assert h["fault_words"] == {}  # "0" and "--" are not faults
    assert planning.health_fault_flag(h) is False
    assert planning.bms_stall_hints(h) == []
    assert planning.bms_summary_line(h).startswith("BMS: SOH 97%, cells 12")


def test_parse_missing_and_faults(planning):
    assert planning.parse_battery_health(None) is None
    assert planning.parse_battery_health({"Time": "x"}) is None
    faulty = {**_RECORD, "DeyeBatBatteryAlarm1(NA)/10021": "4", "Pack2Fault1(NA)/10098": "2",
              "DeyeBatBatteryTemp(℃)/10008": "6", "DeyeBatChargeLimitCurrent(A)/10013": "20",
              "Pack1MaxCell(V)/10054": "3.400"}
    h = planning.parse_battery_health(faulty)
    assert h["fault_words"] == {"DeyeBatBatteryAlarm1": "4", "Pack2Fault1": "2"}
    assert planning.health_fault_flag(h) is True
    hints = planning.bms_stall_hints(h)
    assert any("cold" in x for x in hints) and any("imbalance" in x for x in hints)
    assert any("limit 20 A" in x for x in hints) and any("fault" in x for x in hints)
    # SOH falls back to the weakest pack when the battery-level register is missing
    no_total = {k: v for k, v in _RECORD.items() if not k.startswith("DeyeBatBatterySoh")}
    assert planning.parse_battery_health(no_total)["soh_pct"] == 97.4


def test_fault_flag_includes_health(planning):
    assert planning.inverter_fault_flag(None, None, True) is True
    assert planning.inverter_fault_flag([], None, False) is False
    assert planning.inverter_fault_flag(None, None, None) is None


def test_trend_needs_span_and_computes_wear(planning):
    assert planning.battery_health_trend([])["fade_pct_per_year"] is None
    short = [{"date": "2026-11-01", "soh_pct": 97}, {"date": "2026-11-10", "soh_pct": 96.9}]
    assert planning.battery_health_trend(short)["fade_pct_per_year"] is None  # < 30 days
    history = [
        {"date": "2026-01-01", "soh_pct": 98.0, "cycles_max": 400, "discharge_total_kwh": 3000.0},
        {"date": "2026-04-01", "soh_pct": None},  # ignored
        {"date": "2026-07-02", "soh_pct": 97.0, "cycles_max": 500, "discharge_total_kwh": 3800.0},
    ]
    t = planning.battery_health_trend(history, battery_cost_gbp=4000, lifetime_kwh=60000)
    assert t["points"] == 2 and t["days"] == 182 and t["soh_change_pct"] == -1.0
    assert t["fade_pct_per_1000_cycles"] == 10.0 and t["discharged_kwh"] == 800.0
    assert t["measured_wear_p_per_kwh"] == 5.0  # £4000 x 1% / 800 kWh
    assert t["configured_wear_p_per_kwh"] == 6.67


def test_ledger_point(planning):
    h = planning.parse_battery_health(_RECORD)
    assert planning.ledger_point("2026-11-04", h) == {
        "date": "2026-11-04", "soh_pct": 97.0, "cycles_max": 508.0, "discharge_total_kwh": 4248.6,
    }
    assert planning.ledger_point("2026-11-04", None) is None
