"""Phase 47: KPI statistics sensor values."""


def test_values_map_fields(planning):
    out = planning.kpi_sensor_values({"avoidable_import_gbp": 0.7, "self_sufficiency_pct": 55.2, "unused_charge_kwh": 0.0})
    assert out["kpi_avoidable_import"] == 0.7
    assert out["kpi_self_sufficiency"] == 55.2
    assert out["kpi_unused_charge"] == 0.0  # a real zero stays zero


def test_missing_or_bad_is_none_not_zero(planning):
    out = planning.kpi_sensor_values({"export_peak_gbp": None, "over_limit_import_kwh": "x", "unused_charge_kwh": True})
    assert out["kpi_export_peak"] is None and out["kpi_over_limit_import"] is None
    assert out["kpi_unused_charge"] is None
    assert all(v is None for v in planning.kpi_sensor_values(None).values())


def test_sensor_table_unique_and_typed(planning):
    keys = [k for k, *_ in planning.KPI_SENSORS]
    assert len(keys) == len(set(keys))
    assert {kind for *_, kind in planning.KPI_SENSORS} <= {"money", "measurement"}
