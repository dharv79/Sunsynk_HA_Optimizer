"""Phase 56: intraday solar re-scaling from PV produced so far."""


def test_none_before_ten_or_without_data(planning):
    assert planning.intraday_forecast_scale(3.0, 2.0, 9.5) is None
    assert planning.intraday_forecast_scale(None, 2.0, 12.0) is None
    assert planning.intraday_forecast_scale(3.0, None, 12.0) is None


def test_needs_one_kwh_forecast_so_far(planning):
    assert planning.intraday_forecast_scale(2.0, 0.9, 12.0) is None
    assert planning.intraday_forecast_scale(2.0, 1.0, 12.0) == 2.0


def test_zero_actual_is_a_real_dull_day_not_missing(planning):
    assert planning.intraday_forecast_scale(0.0, 2.0, 12.0) == 0.5


def test_clamped(planning):
    assert planning.intraday_forecast_scale(10.0, 2.0, 12.0) == 2.0
    assert planning.intraday_forecast_scale(0.1, 4.0, 12.0) == 0.5


def test_damped_between_ten_and_eleven(planning):
    assert planning.intraday_forecast_scale(4.0, 2.0, 10.0) == 1.0
    assert planning.intraday_forecast_scale(4.0, 2.0, 10.5) == 1.5
    assert planning.intraday_forecast_scale(4.0, 2.0, 11.0) == 2.0


def test_scaled_solar(planning):
    assert planning.scaled_solar_kwh(None, 1.5) is None
    assert planning.scaled_solar_kwh(4.0, None) == 4.0
    assert planning.scaled_solar_kwh(4.0, 0.5) == 2.0


def test_forecast_so_far_from_hourly(planning):
    hourly = {8: 0.5, 9: 1.0, 10: 1.5, 11: 2.0}
    assert planning.solar_until_kwh(hourly, 0.0, 10.5) == 2.25
