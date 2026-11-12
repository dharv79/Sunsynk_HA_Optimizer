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


# --------------------------------------------------------------------------- #
# Phase 81: security sweep                                                     #
# --------------------------------------------------------------------------- #

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "sunsynk_optimizer"


def test_redact_config_masks_every_secret_key_at_any_depth(planning):
    secrets = ("password", "username", "access_token", "refresh_token", "api_key", "client_secret", "email", "plant_id", "inverter_serial", "account_number")
    data = {k: "x" for k in secrets} | {"keep": 1, "rows": [{"token": "t", "n": 2}, ("a", {"password": "p"})], "deep": {"more": {"secret": "s"}}}
    out = planning.redact_config(data)
    assert all(out[k] == "**REDACTED**" for k in secrets)
    assert out["keep"] == 1
    assert out["rows"] == [{"token": "**REDACTED**", "n": 2}, ["a", {"password": "**REDACTED**"}]]
    assert out["deep"] == {"more": {"secret": "**REDACTED**"}}
    assert data["rows"][0]["token"] == "t"                      # the original is not mutated


def test_url_segment_cannot_change_the_route(planning):
    assert planning.url_segment(" 2411132500 ") == "2411132500"
    assert planning.url_segment("../../oauth?x=1&y") == "..%2F..%2Foauth%3Fx%3D1%26y"
    paths = planning.event_probe_paths("sn/../x", "1?a=b")
    assert all("/../" not in p and "?a=b" not in p for p in paths)


def test_api_quotes_every_id_in_urls():
    src = (PKG / "api.py").read_text("utf-8")
    assert re.findall(r"\{(?:plant_id|inverter_sn)\}", src) == []   # every interpolation goes through _seg()
    assert "ssl=False" not in src and "verify_ssl=False" not in src
    assert src.count("timeout=_TIMEOUT") >= 5


def test_card_resource_serves_only_bundled_files():
    from importlib import util
    spec = util.spec_from_file_location("card_resource_t", PKG / "card_resource.py")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    on_disk = sorted(p.name for p in (PKG / "www").iterdir())
    assert sorted(mod.CARD_FILES) == on_disk                     # www/ holds only the registered cards
    assert all(re.fullmatch(r"[a-z0-9-]+\.js", f) for f in mod.CARD_FILES)


def test_cards_use_innerhtml_only_for_static_markup():
    for card in (PKG / "www").glob("*.js"):
        src = card.read_text("utf-8")
        assert "this._cfg" not in "".join(re.findall(r"innerHTML\s*=\s*`[^`]*`", src)), card.name
        assert "hass.states" not in "".join(re.findall(r"innerHTML\s*=\s*`[^`]*`", src)), card.name


def test_tariff_overrides_parse_safely(planning):
    for junk in ("", "   ", "not json", "[1,2]", "__import__('os')", '{"vat_percent": "20"}', '{"agile_region": "../x"}', None, 7):
        assert planning.tariff_vat_percent(junk) == planning.TARIFF_VAT_PERCENT
        assert planning.agile_region_override(junk) is None
    assert planning.agile_region_override('{"agile_region": "c"}') == "C"


def test_workflows_pin_actions_and_limit_permissions():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text("utf-8")
        for ref in re.findall(r"uses:\s*(\S+)", text):
            if ref.startswith("./.github/workflows/"):
                continue  # same-repo reusable workflow, runs at the same commit
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"{wf.name}: {ref} not pinned to a SHA"
        assert "permissions:" in text, wf.name
    assert "contents: read" in (ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
    assert "Refusing release" in (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")


# --------------------------------------------------------------------------- #
# Phase 93: options validation and redaction by value                          #
# --------------------------------------------------------------------------- #


def test_time_fields_must_be_hhmm(planning):
    assert all(planning.valid_hhmm(t) for t in ("00:00", "09:05", "16:00", "23:59"))
    assert not any(planning.valid_hhmm(t) for t in ("16.00", "24:00", "9:00", "16:60", "16:00:00", "", None, 1600))
    fields = {"a_start": "16.00", "a_end": "19:00", "b_start": "02:00", "b_end": "02:00"}
    assert planning.time_field_errors(fields, (("a_start", "a_end"), ("b_start", "b_end"))) == {
        "a_start": "invalid_time", "b_end": "same_time",
    }
    assert planning.time_field_errors({"s": "02:00", "e": "05:00"}, (("s", "e"),)) == {}
    assert planning.time_field_errors({"s": "00:00", "e": "00:00"}) == {}   # no pair given: equal is allowed


def test_config_time_problems_lists_bad_saved_times(planning):
    charges = [{"startRange": "02:00", "endRange": "05:00"}, {"startRange": "16.00", "endRange": "19:00"}]
    flux = [{"startTime": "02:00", "endTime": "04:30"}, {"startTime": "16:00", "endTime": None}]
    assert planning.config_time_problems(charges, flux) == ["tariff row 2 start '16.00'", "Flux 2 end 'None'"]
    assert planning.config_time_problems(charges[:1], flux[:1]) == []
    assert planning.config_time_problems(None, "junk") == []


def test_diagnostics_redacts_identifiers_by_value(planning):
    config = {
        "plant_id": "2411132500", "inverter_serial": "SN99ABC123", "username": "someone@example.com",
        "octopus_import_rates_entity": "event.octopus_energy_electricity_21l4567890_1900012345678_current_day_rates",
        "auto_away_persons": "person.alex, person.sam", "auto_away_calendar": "calendar.family",
        "charge_rate": 2.5, "weather_entity": "weather.home", "tomorrow_forecast_sensor": "",
    }
    state = {
        "last_error": "API push failed: 500 for https://api.example/api/v1/plant/2411132500/income",
        "last_api_result": {"ok": False, "error": "GET /common/setting/sn99abc123/read failed"},
        "last_flux2_action": {"payload": {"id": "2411132500", "fluxProducts": [{"targetSoc": 85}]}},
        "auto_away": {"home": ["person.alex"], "event": "calendar.family"},
        "battery_soc": 82,
    }
    paired = [{"date": "2026-11-10", "note": "meter 21L4567890", "plan_kwh": 6.6}]
    out = planning.redact_diagnostics(config, state, paired)
    text = repr(out).lower()
    for secret in ("2411132500", "sn99abc123", "someone@example.com", "21l4567890", "1900012345678",
                   "person.alex", "person.sam", "calendar.family"):
        assert secret not in text, secret
    assert out["config"]["charge_rate"] == 2.5 and out["config"]["weather_entity"] == "weather.home"
    assert out["state"]["battery_soc"] == 82 and out["paired_days"][0]["plan_kwh"] == 6.6
    assert out["state"]["last_flux2_action"]["payload"]["fluxProducts"] == [{"targetSoc": 85}]
    assert "octopus_energy_electricity" in out["config"]["octopus_import_rates_entity"]   # only the ids go
    assert planning.sensitive_values({"plant_id": "12", "charge_rate": 2.5}) == []          # too short to mask safely
