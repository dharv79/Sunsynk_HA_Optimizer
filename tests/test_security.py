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
