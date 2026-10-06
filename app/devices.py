"""Resolve a farmer's device without sharing ThingSpeak account credentials."""
import re

import httpx
from fastapi import HTTPException

from .config import settings
from .schemas import ThingSpeakConfig


FIELD_LABELS = {
    "water_temperature": {"temperature", "temperaturec", "temparaturec", "temperaturecelsius", "watertemperature", "watertemperaturec", "watertemperaturecelsius", "temp", "tempc", "watertemp"},
    "ph": {"ph", "waterph"},
    "dissolved_oxygen": {"do", "domgl", "dissolvedoxygen", "dissolvedoxygenmgl", "oxygenmgl"},
    "turbidity": {"turbidity", "turbidityntu", "ntuturbidity"},
    "turbidity_voltage": {"turbiditysensorvoltagev", "turbidityvoltagev", "turbidityvoltage"},
}


def infer_fields(channel):
    mapping = {}
    for parameter, labels in FIELD_LABELS.items():
        matches = [n for n in range(1, 9)
                   if re.sub(r"[^a-z0-9]", "", str(channel.get(f"field{n}", "")).lower()) in labels]
        if len(matches) > 1:
            raise HTTPException(422, "A sensor field is ambiguous. Ask your device provider to finish setup, or select the available fields under Advanced sensor setup.")
        if matches:
            mapping[parameter] = matches[0]
    if not mapping:
        raise HTTPException(422, "The sensor fields could not be identified automatically. Ask your device provider to finish setup, or select the available fields under Advanced sensor setup.")
    return ThingSpeakConfig(channel_id=channel["id"], field_map=mapping).field_map


def resolve_connection(payload, store, pond_id=None):
    """Verify readability before any pond/connection write. Empty channels are valid."""
    previous = store.connection(pond_id) if pond_id else None
    same_channel = previous and previous["channel_id"] == payload.channel_id
    device = store.provisioned_device(payload.channel_id)
    key = payload.read_api_key
    if key is None:
        key = (device or {}).get("read_api_key")
        if key is None:
            key = (previous or {}).get("read_api_key", "") if same_channel else ""
    mapping = (device or {}).get("field_map") or payload.field_map
    if mapping is None and same_channel:
        mapping = previous["field_map"]
    if previous and mapping and store.latest_reading(pond_id) and (
        not same_channel or previous["field_map"] != mapping
    ):
        raise HTTPException(409, "This pond already has sensor history. Add a separate pond for a different channel or field mapping.")

    params = {"results": 1}
    if key:
        params["api_key"] = key
    try:
        with httpx.Client(timeout=settings.upstream_timeout_seconds, follow_redirects=False) as client:
            response = client.get(f"https://api.thingspeak.com/channels/{payload.channel_id}/feeds.json", params=params)
        if response.status_code in (400, 401, 403, 404):
            raise HTTPException(422, "This channel is unavailable or private. Ask your device provider to assign it to your farmer account, or enter its Read API Key under Advanced sensor setup. Use the numeric Channel ID, not your email or password.")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("channel"), dict) or not isinstance(data.get("feeds"), list):
            raise HTTPException(422, "ThingSpeak did not allow access to this channel. Check the Channel ID and device setup; a private channel needs its Read API Key.")
        channel = data["channel"]
        if channel.get("id") != payload.channel_id:
            raise ValueError("Channel identity mismatch.")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "ThingSpeak could not be reached. Your pond was not changed; try saving again shortly.") from exc
    if mapping is None:
        mapping = infer_fields(channel)
    else:
        mapping = ThingSpeakConfig(channel_id=payload.channel_id, field_map=mapping).field_map
    if any(not channel.get(f"field{n}") for n in mapping.values()):
        raise HTTPException(422, "A selected sensor field is disabled on this ThingSpeak channel. Check Advanced sensor setup with your device provider.")
    return {"channel_id": payload.channel_id, "read_api_key": key, "field_map": mapping}
