# Copyright 2026 Dave Harvey
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Open-Meteo forecast API: hourly sunlight on the panels for the solar forecast (phase 136, shadow).

No account or key. The location is rounded to 2 decimal places (about 1 km) before it is
sent. Every failure returns None so the plan logs the Open-Meteo forecast as unavailable and
carries on unchanged.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def async_fetch_tilted(
    session: aiohttp.ClientSession, lat: float, lon: float, tilt: float, azimuth: float, cloud: bool = False,
) -> dict[str, list[Any]] | None:
    """{"time", "gti"[, "cloud"]} hourly lists (UTC stamps) for one panel orientation; None on any failure.

    ``azimuth`` is Open-Meteo's convention (0 south, -90 east, 90 west). One day back is
    included so the local day is complete when it starts before UTC midnight.
    """
    hourly = "global_tilted_irradiance" + (",cloud_cover" if cloud else "")
    params = {
        "latitude": round(float(lat), 2), "longitude": round(float(lon), 2), "hourly": hourly,
        "tilt": float(tilt), "azimuth": float(azimuth), "timezone": "GMT", "past_days": 1, "forecast_days": 2,
    }
    try:
        async with session.get(OPEN_METEO_API, params=params, timeout=_TIMEOUT) as response:
            response.raise_for_status()
            data = await response.json()
        block = data.get("hourly") or {}
        times, gti = block.get("time"), block.get("global_tilted_irradiance")
        if not isinstance(times, list) or not isinstance(gti, list) or len(times) != len(gti):
            raise ValueError("unexpected hourly shape")
        out: dict[str, list[Any]] = {"time": times, "gti": gti}
        if cloud:
            cover = block.get("cloud_cover")
            out["cloud"] = cover if isinstance(cover, list) and len(cover) == len(times) else []
        return out
    except Exception as exc:  # network, HTTP or JSON shape
        _LOGGER.warning("Open-Meteo forecast fetch failed: %s", exc)
        return None


# Phase 138: archived forecasts for the backfill. The Previous Runs API keeps the forecast each
# hour had one day ahead (closest to what the 01:55 plan saw); the Historical Forecast API
# stitches the first hours of each run and is the fallback.
PREVIOUS_RUNS_API = "https://previous-runs-api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_API = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ARCHIVES = (
    ("previous_runs", PREVIOUS_RUNS_API, "_previous_day1", 1),
    ("historical_forecast", HISTORICAL_FORECAST_API, "", 0),
)
_ARCHIVE_TIMEOUT = aiohttp.ClientTimeout(total=60)


async def async_fetch_tilted_archive(
    session: aiohttp.ClientSession, url: str, suffix: str, lat: float, lon: float, tilt: float, azimuth: float,
    start: str, end: str, cloud: bool = False,
) -> dict[str, list[Any]] | None:
    """{"time", "gti"[, "cloud"]} hourly lists (UTC stamps) from one archive for ``start``..``end``
    (ISO dates, inclusive; one day before ``start`` is added so local days are complete). Same
    rounding and azimuth convention as ``async_fetch_tilted``; None on any failure."""
    gti_key, cloud_key = f"global_tilted_irradiance{suffix}", f"cloud_cover{suffix}"
    try:
        first = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
        params = {
            "latitude": round(float(lat), 2), "longitude": round(float(lon), 2),
            "hourly": gti_key + (f",{cloud_key}" if cloud else ""), "tilt": float(tilt), "azimuth": float(azimuth),
            "timezone": "GMT", "start_date": first, "end_date": end,
        }
        async with session.get(url, params=params, timeout=_ARCHIVE_TIMEOUT) as response:
            response.raise_for_status()
            data = await response.json()
        block = data.get("hourly") or {}
        times, gti = block.get("time"), block.get(gti_key)
        if not isinstance(times, list) or not isinstance(gti, list) or len(times) != len(gti):
            raise ValueError("unexpected hourly shape")
        if not any(isinstance(v, (int, float)) for v in gti):
            raise ValueError("no irradiance in range")
        out: dict[str, list[Any]] = {"time": times, "gti": gti}
        if cloud:
            cover = block.get(cloud_key)
            out["cloud"] = cover if isinstance(cover, list) and len(cover) == len(times) else []
        return out
    except Exception as exc:  # network, HTTP or JSON shape
        _LOGGER.warning("Open-Meteo archive fetch failed: %s", exc)
        return None
