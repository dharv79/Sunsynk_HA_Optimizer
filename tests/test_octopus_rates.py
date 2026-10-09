"""Tests for phase 18: tariff rates read from the Octopus Energy integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def _flux_day(day: date, offpeak=0.2297, day_rate=0.2871, peak=0.4019) -> list[dict]:
    """Half-hourly Flux slots for one day in the Octopus integration's `rates` shape (£/kWh)."""
    rows = []
    start = datetime.combine(day, datetime.min.time(), tzinfo=LONDON)
    for i in range(48):
        slot = start + timedelta(minutes=30 * i)
        value = offpeak if 2 <= slot.hour < 5 else peak if 16 <= slot.hour < 19 else day_rate
        rows.append({
            "start": slot.isoformat(),
            "end": (slot + timedelta(minutes=30)).isoformat(),
            "value_inc_vat": value,
            "is_capped": False,
        })
    return rows


TODAY = date(2026, 10, 9)


def test_rates_event_gives_window_price_in_pence(flux_helpers):
    attrs = {"rates": _flux_day(TODAY)}
    assert flux_helpers.octopus_rate_pence_per_kwh("2026-10-09T00:00:00", attrs, "16:00", "19:00", TODAY, LONDON) == 40.19
    assert flux_helpers.octopus_rate_pence_per_kwh("x", attrs, "02:00", "05:00", TODAY, LONDON) == 22.97
    assert flux_helpers.octopus_rate_pence_per_kwh("x", attrs, "05:00", "16:00", TODAY, LONDON) == 28.71


def test_window_average_is_time_weighted(flux_helpers):
    attrs = {"rates": _flux_day(TODAY)}
    # 15:00-17:00 = 1h day rate + 1h peak rate.
    assert flux_helpers.octopus_rate_pence_per_kwh("x", attrs, "15:00", "17:00", TODAY, LONDON) == round((28.71 + 40.19) / 2, 3)


def test_prefers_on_date_then_latest_covered_date(flux_helpers):
    attrs = {"rates": _flux_day(TODAY) + _flux_day(TODAY + timedelta(days=1), peak=0.45)}
    assert flux_helpers.octopus_rate_pence_per_kwh("x", attrs, "16:00", "19:00", TODAY, LONDON) == 40.19
    # A date the rates don't cover (e.g. KPIs for yesterday) uses the latest day.
    assert flux_helpers.octopus_rate_pence_per_kwh("x", attrs, "16:00", "19:00", TODAY - timedelta(days=3), LONDON) == 45.0


def test_current_rate_sensor_block(flux_helpers):
    attrs = {"start": "2026-10-09T16:00:00+01:00", "end": "2026-10-09T19:00:00+01:00"}
    assert flux_helpers.octopus_rate_pence_per_kwh("0.4019", attrs, "16:00", "19:00", TODAY, LONDON) == 40.19
    # The block doesn't overlap the off-peak window → unknown, not 0p.
    assert flux_helpers.octopus_rate_pence_per_kwh("0.4019", attrs, "02:00", "05:00", TODAY, LONDON) is None


def test_unusable_entities_return_none(flux_helpers):
    rate = flux_helpers.octopus_rate_pence_per_kwh
    assert rate("unavailable", {}, "16:00", "19:00", TODAY, LONDON) is None
    assert rate("unknown", None, "16:00", "19:00", TODAY, LONDON) is None
    attrs = {"start": "2026-10-09T16:00:00+01:00", "end": "2026-10-09T19:00:00+01:00"}
    assert rate("unavailable", attrs, "16:00", "19:00", TODAY, LONDON) is None
    bad_rows = {"rates": [{"start": "garbage", "end": None, "value_inc_vat": 0.4}, "not a dict"]}
    assert rate("x", bad_rows, "16:00", "19:00", TODAY, LONDON) is None


def test_sensor_rates_override_charges_with_source(flux_helpers):
    charges = flux_helpers.default_charges()
    prices, sources = flux_helpers.tariff_prices_pence(charges, {"peak": 40.19, "export_peak": None})
    assert prices["peak"] == 40.19 and sources["peak"] == "octopus"
    # Unavailable sensor band falls back to charges.
    assert prices["export_peak"] == flux_helpers.kpi_prices_pence(charges)["export_peak"]
    assert sources["export_peak"] == "charges"
    assert flux_helpers.price_source(sources, "peak") == "octopus"
    assert flux_helpers.price_source(sources, "export_peak", "offpeak") == "charges"
    assert flux_helpers.price_source(sources, "peak", "offpeak") == "mixed"


def test_no_sensor_matches_charges_only_prices(flux_helpers):
    charges = flux_helpers.default_charges()
    prices, sources = flux_helpers.tariff_prices_pence(charges)
    assert prices == flux_helpers.kpi_prices_pence(charges)
    assert flux_helpers.price_source(sources, *flux_helpers.TARIFF_BANDS) == "charges"


def test_missing_everywhere_is_none_never_zero(flux_helpers):
    prices, sources = flux_helpers.tariff_prices_pence([], {"peak": None})
    assert set(prices.values()) == {None}
    assert flux_helpers.price_source(sources, "peak") is None
