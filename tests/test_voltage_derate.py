"""Phase 83: suspected high-voltage export derating (tally only)."""

from __future__ import annotations

from conftest import _planning as planning

suspect = planning.voltage_derate_suspect


def test_flags_curtailed_solar_at_high_voltage():
    # 4 kW PV, 0.5 kW export, 0.4 kW load, battery full, 254 V: most solar is going nowhere.
    assert suspect(254.0, -500.0, 4000.0, 400.0, 100.0)


def test_not_suspect_when_solar_is_used_or_voltage_normal():
    assert not suspect(250.0, -500.0, 4000.0, 400.0, 100.0)            # voltage fine
    assert not suspect(254.0, -3500.0, 4000.0, 400.0, 100.0)           # exporting nearly all of it
    assert not suspect(254.0, -500.0, 4000.0, 400.0, 90.0)             # battery can still absorb
    assert not suspect(254.0, -100.0, 300.0, 50.0, 100.0)              # too little PV to judge


def test_missing_inputs_are_never_suspect():
    for i in range(5):
        args = [254.0, -500.0, 4000.0, 400.0, 100.0]
        args[i] = None
        assert suspect(*args) is False


def test_edges():
    assert suspect(253.0, 0.0, 1000.0, 0.0, 99.0)                      # import-side pac counts as no export
    assert not suspect(252.9, 0.0, 1000.0, 0.0, 99.0)
