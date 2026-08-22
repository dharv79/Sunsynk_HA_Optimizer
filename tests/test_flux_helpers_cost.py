"""Tests for flux_helpers.peak_import_price_pence_per_kwh (v1.0.11 Part 3)."""

from __future__ import annotations


def test_exact_window_match_against_real_default_charges(flux_helpers):
    charges = flux_helpers.default_charges()
    assert flux_helpers.peak_import_price_pence_per_kwh(charges) == 38.88


def test_falls_back_to_midpoint_containing_row_when_no_exact_match(flux_helpers):
    charges = [
        {"price": 10.0, "status": "import", "startRange": "00:00", "endRange": "15:30"},
        {"price": 99.0, "status": "import", "startRange": "15:30", "endRange": "20:00"},
    ]
    # No row matches 16:00-19:00 exactly, but its midpoint (17:30) falls inside
    # the second row.
    assert flux_helpers.peak_import_price_pence_per_kwh(charges) == 99.0


def test_empty_charges_returns_none(flux_helpers):
    assert flux_helpers.peak_import_price_pence_per_kwh([]) is None


def test_no_matching_import_row_returns_none(flux_helpers):
    charges = [{"price": 10.0, "status": "import", "startRange": "00:00", "endRange": "10:00"}]
    assert flux_helpers.peak_import_price_pence_per_kwh(charges) is None


def test_export_rows_are_excluded_even_on_exact_match(flux_helpers):
    charges = [
        {"price": 27.81, "status": "export", "startRange": "16:00", "endRange": "19:00"},
    ]
    assert flux_helpers.peak_import_price_pence_per_kwh(charges) is None


def test_custom_window_arguments(flux_helpers):
    charges = flux_helpers.default_charges()
    assert flux_helpers.peak_import_price_pence_per_kwh(charges, "02:00", "05:00") == 16.66
