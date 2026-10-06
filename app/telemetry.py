"""Real ThingSpeak ingestion. Missing data never becomes simulated telemetry."""
from datetime import datetime, timezone
import math

import httpx
from fastapi import HTTPException

from .config import settings

RANGES = {"dissolved_oxygen": (0.0, 20.0), "water_temperature": (5.0, 45.0),
          "ph": (4.0, 11.0), "turbidity": (0.0, 400.0)}


def parse_time(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Sensor timestamps must include a timezone.")
    return dt.astimezone(timezone.utc)


def safe_connection(connection):
    if not connection:
        return None
    return {k: connection.get(k) for k in ("channel_id", "field_map", "synced_at")} | {
        "has_read_api_key": bool(connection.get("read_api_key"))}


def quality_flag(reading, previous=None):
    for key, (lo, hi) in RANGES.items():
        value = reading.get(key)
        if value is None:
            return "missing"
        if not math.isfinite(value) or not lo <= value <= hi:
            return "out_of_range"
    if previous and previous.get("quality", "ok") == "ok":
        # Only compare nearby samples; a legitimate day-long change is not
        # equivalent to a spike between consecutive minute readings.
        gap = (parse_time(reading["ts"]) - parse_time(previous["ts"])).total_seconds()
        if 0 < gap <= 300 and (
            abs(reading["water_temperature"] - previous["water_temperature"]) > 4.0
            or abs(reading["dissolved_oxygen"] - previous["dissolved_oxygen"]) > 5.0
        ):
            return "spike"
    return "ok"


def parse_feeds(feeds, field_map, previous=None):
    readings, skipped = [], 0
    now = datetime.now(timezone.utc)
    for feed in feeds:
        try:
            stamp = parse_time(feed["created_at"])
            entry_id = int(feed["entry_id"])
            if entry_id <= 0 or (stamp - now).total_seconds() > 120:
                raise ValueError("Invalid entry.")
        except (ValueError, KeyError, TypeError, OverflowError):
            skipped += 1
            continue
        reading = {"ts": stamp.isoformat(), "entry_id": entry_id, "source": "thingspeak",
                   **{key: None for key in RANGES}}
        for key, field in field_map.items():
            try:
                value = float(feed.get(f"field{field}"))
                reading[key] = value if math.isfinite(value) else None
            except (ValueError, TypeError, OverflowError):
                reading[key] = None
        readings.append(reading)
    readings.sort(key=lambda r: (r["ts"], r["entry_id"]))
    for reading in readings:
        reading["quality"] = quality_flag(reading, previous)
        previous = reading
    return readings, skipped


def is_stale(reading):
    age = (datetime.now(timezone.utc) - parse_time(reading["ts"])).total_seconds()
    return age > settings.telemetry_stale_seconds or age < -120


def record(pond, store):
    connection = store.connection(pond["id"])
    if not connection:
        raise HTTPException(409, "Connect this pond to ThingSpeak using Edit pond.")
    previous = store.latest_reading(pond["id"])
    synced = connection.get("synced_at")
    if previous and synced and (
        datetime.now(timezone.utc) - parse_time(synced)
    ).total_seconds() < settings.telemetry_interval_seconds:
        reading = previous
    else:
        params = {"results": min(8000, max(1, settings.thingspeak_history_results))}
        if connection.get("read_api_key"):
            params["api_key"] = connection["read_api_key"]
        if previous:
            params["start"] = parse_time(previous["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with httpx.Client(timeout=settings.upstream_timeout_seconds, follow_redirects=False) as client:
                res = client.get(
                    f"https://api.thingspeak.com/channels/{connection['channel_id']}/feeds.json", params=params)
            if not res.is_success:
                raise ValueError("Upstream rejected request.")
            data = res.json()
            if not isinstance(data, dict) or not isinstance(data.get("feeds"), list):
                raise ValueError("Invalid response.")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Cannot read ThingSpeak. Check the Channel ID, Read API Key, and sensor connection.") from exc
        readings, _ = parse_feeds(data["feeds"], connection["field_map"], previous)
        store.insert_readings(pond["id"], readings)
        store.mark_synced(pond["id"])
        reading = store.latest_reading(pond["id"])
    if not reading:
        raise HTTPException(409, "ThingSpeak has no readings for this pond yet.")
    return reading
