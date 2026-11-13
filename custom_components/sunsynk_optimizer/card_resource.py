"""Serve the bundled Lovelace cards so the generated dashboard needs no manual resource."""

from __future__ import annotations

import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

STATIC_URL = "/sunsynk_optimizer_static"
CARD_FILES = (
    "sunsynk-optimizer-flow-card.js",
    "sunsynk-optimizer-day-chart.js",
    "sunsynk-optimizer-heatmap.js",
)


def card_url(file: str, version: str) -> str:
    """Versioned URL so a new release is never served from the browser cache."""
    return f"{STATIC_URL}/{file}?v={version}"


def _version() -> str:
    try:
        return json.loads((Path(__file__).parent / "manifest.json").read_text("utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "0"


async def async_register_card(hass) -> None:
    """Expose www/ and load each card in every dashboard. Never raises: the cards are cosmetic."""
    try:
        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import StaticPathConfig

        www = Path(__file__).parent / "www"
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(www), False)])
        version = await hass.async_add_executor_job(_version)  # file read: keep it off the event loop
        for file in CARD_FILES:
            add_extra_js_url(hass, card_url(file, version))
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Could not register the Sunsynk Optimizer cards: %s", err)
