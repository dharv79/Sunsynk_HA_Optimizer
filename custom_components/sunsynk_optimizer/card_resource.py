"""Serve the bundled Lovelace card so the generated dashboard needs no manual resource."""

from __future__ import annotations

import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

CARD_FILE = "sunsynk-optimizer-flow-card.js"
CARD_URL = "/sunsynk_optimizer_static/" + CARD_FILE


def card_url(version: str) -> str:
    """Versioned URL so a new release is never served from the browser cache."""
    return f"{CARD_URL}?v={version}"


def _version() -> str:
    try:
        return json.loads((Path(__file__).parent / "manifest.json").read_text("utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "0"


async def async_register_card(hass) -> None:
    """Expose www/<card>.js and load it in every dashboard. Never raises: the card is cosmetic."""
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        path = Path(__file__).parent / "www" / CARD_FILE
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), False)])
        version = await hass.async_add_executor_job(_version)  # file read: keep it off the event loop
        add_extra_js_url(hass, card_url(version))
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not register the Sunsynk Optimizer flow card: %s", err)
