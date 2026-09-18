"""Open-Meteo client.

Fixes the two real bugs in the previous version:

1. Hour alignment. Open-Meteo returns hourly data from 00:00 local, not from
   now. Slicing [:hours] meant a 6 PM request simulated this morning. We now
   locate the current hour in the returned series and slice from there.

2. Call volume. The old frontend refreshed every 5 s and refetched every time.
   Results are cached per rounded coordinate for 15 minutes.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import settings
from .engine import HourWeather

_cache: dict[tuple, tuple[float, dict]] = {}
_lock = asyncio.Lock()


class WeatherUnavailable(Exception):
    """Upstream forecast could not be retrieved."""


async def _fetch(latitude: float, longitude: float) -> dict:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": (
            "temperature_2m,relative_humidity_2m,wind_speed_10m,"
            "shortwave_radiation,cloud_cover"
        ),
        "forecast_days": 3,
        "timezone": "auto",
    }

    last_error: Exception | None = None
    for attempt in range(settings.weather_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.weather_timeout_seconds) as client:
                response = await client.get(settings.weather_url, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as exc:                      # noqa: BLE001
            last_error = exc
            if attempt < settings.weather_retries:
                await asyncio.sleep(0.6 * (2 ** attempt))

    raise WeatherUnavailable(str(last_error))


def _current_index(times: list[str], utc_offset_seconds: int) -> int:
    """Index of the hour that contains 'now' in pond-local time."""
    local_now = datetime.now(timezone.utc) + timedelta(seconds=utc_offset_seconds)
    local_now = local_now.replace(tzinfo=None)
    for i, t in enumerate(times):
        try:
            stamp = datetime.fromisoformat(t)
        except ValueError:
            continue
        if stamp + timedelta(hours=1) > local_now:
            return i
    return 0


async def forecast(latitude: float, longitude: float, hours: int) -> dict:
    hours = max(1, min(hours, settings.max_horizon_hours))
    key = (round(latitude, 2), round(longitude, 2))

    async with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < settings.weather_cache_seconds:
            raw = hit[1]
        else:
            raw = await _fetch(latitude, longitude)
            _cache[key] = (time.time(), raw)

    hourly = raw["hourly"]
    offset = int(raw.get("utc_offset_seconds", 0))
    start = _current_index(hourly["time"], offset)
    end = start + hours

    def take(name: str, default: float) -> list[float]:
        series = hourly.get(name) or []
        out = [v if v is not None else default for v in series[start:end]]
        while len(out) < hours:
            out.append(out[-1] if out else default)
        return out

    return {
        "source": "open-meteo",
        "timezone": raw.get("timezone", "UTC"),
        "utc_offset_seconds": offset,
        "time": hourly["time"][start:end],
        "air_temperature": take("temperature_2m", 28.0),
        "relative_humidity": take("relative_humidity_2m", 75.0),
        "wind_speed_ms": [round(v / 3.6, 2) for v in take("wind_speed_10m", 8.0)],
        "solar_radiation": take("shortwave_radiation", 0.0),
        "cloud_cover": take("cloud_cover", 40.0),
    }


def synthetic(hours: int, seed: int | None = None) -> dict:
    """Clearly-labelled fallback so the twin degrades instead of failing."""
    rng = random.Random(seed if seed is not None else int(time.time() // 3600))
    now = datetime.now()
    times, air, rh, wind, solar, cloud = [], [], [], [], [], []

    for i in range(hours):
        stamp = now + timedelta(hours=i)
        hour = stamp.hour + stamp.minute / 60
        times.append(stamp.strftime("%Y-%m-%dT%H:00"))
        solar.append(max(0.0, 860 * math.sin(math.pi * (hour - 6) / 12)))
        air.append(28.5 + 4.2 * math.sin(2 * math.pi * (hour - 9) / 24))
        rh.append(72 + 14 * math.cos(2 * math.pi * (hour - 5) / 24))
        wind.append(max(0.2, 2.1 + rng.uniform(-0.9, 0.9)))
        cloud.append(clamp_cloud(38 + rng.uniform(-22, 22)))

    return {
        "source": "synthetic",
        "timezone": "local",
        "utc_offset_seconds": 0,
        "time": times,
        "air_temperature": air,
        "relative_humidity": rh,
        "wind_speed_ms": wind,
        "solar_radiation": solar,
        "cloud_cover": cloud,
    }


def clamp_cloud(v: float) -> float:
    return max(0.0, min(100.0, v))


def to_hours(payload: dict) -> list[HourWeather]:
    return [
        HourWeather(
            time=payload["time"][i],
            air_temperature=payload["air_temperature"][i],
            wind_speed_ms=payload["wind_speed_ms"][i],
            solar_radiation=payload["solar_radiation"][i],
            relative_humidity=payload["relative_humidity"][i],
            cloud_cover=payload["cloud_cover"][i],
        )
        for i in range(len(payload["time"]))
    ]


async def resolve(latitude: float, longitude: float, hours: int) -> tuple[list[HourWeather], str]:
    """Live forecast if possible, synthetic if not. Always says which."""
    try:
        payload = await forecast(latitude, longitude, hours)
    except WeatherUnavailable:
        payload = synthetic(hours)
    return to_hours(payload), payload["source"]
