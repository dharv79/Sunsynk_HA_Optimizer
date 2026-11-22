"""Phase 128 review fix: merge_entry_data must not share options' nested lists with the entry."""

from __future__ import annotations


def test_merged_charges_list_is_not_the_options_object(flux_helpers):
    options = {"charges": [{"price": 1.0, "startRange": "00:00", "endRange": "01:00"}]}
    merged = flux_helpers.merge_entry_data({}, options)
    assert merged["charges"] == options["charges"]
    assert merged["charges"] is not options["charges"]


def test_in_place_edit_of_the_merged_list_does_not_mutate_the_original_options(flux_helpers):
    """Mirrors the options-flow bug: a step mutates self._working[CONF_CHARGES][idx] in place.

    If merge_entry_data shared the list object, that would also mutate config_entry.options,
    making the final async_create_entry(data=self._working) look unchanged against entry.options
    and HA would skip saving and reloading the edit.
    """
    options = {"charges": [{"price": 1.0, "startRange": "00:00", "endRange": "01:00"}]}
    merged = flux_helpers.merge_entry_data({}, options)
    merged["charges"][0] = {"price": 9.0, "startRange": "00:00", "endRange": "01:00"}
    assert options["charges"][0]["price"] == 1.0


def test_data_and_options_nested_lists_are_independent_copies(flux_helpers):
    data = {"flux_products": [{"startTime": "00:30", "endTime": "04:30", "targetSoc": 85}]}
    merged = flux_helpers.merge_entry_data(data, {})
    merged["flux_products"][0]["targetSoc"] = 50
    assert data["flux_products"][0]["targetSoc"] == 85
