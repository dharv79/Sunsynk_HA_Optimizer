"""Tests for the b37 security hardening."""

from __future__ import annotations


def test_safe_id_passes_legitimate_ids(safe_id):
    assert safe_id("2411132500") == "2411132500"       # numeric plant id
    assert safe_id("abc-DEF_123") == "abc-DEF_123"      # alphanumeric serial
    assert safe_id(" 12 34 ") == "1234"                 # whitespace stripped


def test_safe_id_strips_template_and_quote_chars(safe_id):
    # A value trying to inject a Jinja expression into the dashboard markdown
    # is reduced to harmless alphanumerics.
    assert safe_id("111{{states('x')}}") == "111statesx"
    assert safe_id("a\"'}{b") == "ab"
    assert safe_id("{{ 7 * 7 }}") == "77"
