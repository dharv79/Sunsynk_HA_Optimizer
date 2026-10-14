"""Octopus Energy public API: historic Agile unit rates for the tariff comparison (phase 35).

No account or key needed. Every failure returns None so the comparison reports
Agile as n/a and still prices the other tariffs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

from .planning import newest_agile_product

_LOGGER = logging.getLogger(__name__)

OCTOPUS_API = "https://api.octopus.energy/v1"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MAX_PAGES = 5


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict[str, Any] | None = None) -> Any:
    async with session.get(url, params=params, timeout=_TIMEOUT) as response:
        response.raise_for_status()
        return await response.json()


async def async_fetch_agile_rates(
    session: aiohttp.ClientSession, region: str, period_from: datetime, period_to: datetime
) -> list[dict[str, Any]] | None:
    """Raw standard-unit-rates results for Agile in ``region`` over the period; None on any failure."""
    try:
        listing = await _get_json(
            session,
            f"{OCTOPUS_API}/products/",
            # Products on sale when the month began, so its rates cover the whole month.
            {"brand": "OCTOPUS_ENERGY", "is_variable": "true", "available_at": period_from.isoformat(), "page_size": 100},
        )
        code = newest_agile_product(listing.get("results") or [], period_from)
        if code is None:
            _LOGGER.warning("No Agile product found in the Octopus product list")
            return None
        url: str | None = f"{OCTOPUS_API}/products/{code}/electricity-tariffs/E-1R-{code}-{region}/standard-unit-rates/"
        params: dict[str, Any] | None = {
            "period_from": period_from.isoformat(),
            "period_to": period_to.isoformat(),
            "page_size": 1500,
        }
        results: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            if not url:
                break
            page = await _get_json(session, url, params)
            results.extend(page.get("results") or [])
            url, params = page.get("next"), None  # "next" carries the query string
        return results
    except Exception as exc:  # network, HTTP or JSON shape
        _LOGGER.warning("Agile rates fetch failed: %s", exc)
        return None
