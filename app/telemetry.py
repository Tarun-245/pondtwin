"""Telemetry.

Right now readings are synthesised per pond, but they travel the same path a
real sensor will: generate -> quality check -> persist. When you wire up actual
probes, only `sample()` gets replaced. The QA layer and the store stay.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from . import db
from .engine import do_saturation

# Plausible operating ranges. A reading outside these is flagged, not stored
# as truth, so bad sensors cannot quietly poison the training set later.
RANGES = {
    "dissolved_oxygen": (0.0, 20.0),
    "water_temperature": (5.0, 45.0),
    "ph": (4.0, 11.0),
    "turbidity": (0.0, 400.0),
}


def quality_flag(reading: dict, previous: dict | None) -> str:
    for key, (lo, hi) in RANGES.items():
        value = reading.get(key)
        if value is None:
            return "missing"
        if not (lo <= value <= hi):
            return "out_of_range"

    if previous:
        if abs(reading["water_temperature"] - previous["water_temperature"]) > 4.0:
            return "spike"
        if abs(reading["dissolved_oxygen"] - previous["dissolved_oxygen"]) > 5.0:
            return "spike"
        same = all(
            abs(reading[k] - previous[k]) < 1e-6
            for k in ("dissolved_oxygen", "water_temperature", "ph", "turbidity")
        )
        if same:
            return "stuck"

    return "ok"


def sample(pond: dict) -> dict:
    """Synthetic but physically-shaped pond telemetry."""
    now = datetime.now()
    hour = now.hour + now.minute / 60 + now.second / 3600
    rng = random.Random(int(now.timestamp() // 5) ^ hash(pond["id"]) & 0xFFFF)

    solar = max(0.0, math.sin(math.pi * (hour - 6) / 12))
    depth = pond["depth_m"]

    temp = (
        27.6
        + 2.9 * math.sin(2 * math.pi * (hour - 8.5) / 24) * (2.0 / max(0.8, depth)) ** 0.4
        + rng.uniform(-0.2, 0.2)
    )
    temp = max(22.0, min(34.5, temp))

    sat = do_saturation(temp)
    density = (pond["stock_count"] * pond["avg_weight_g"] / 1000.0) / max(
        1.0, pond["length_m"] * pond["width_m"] * depth
    )

    do = (
        sat * 0.62
        + 2.3 * solar
        - 1.15 * math.cos(2 * math.pi * (hour - 5) / 24)
        - 3.2 * density
        + rng.uniform(-0.12, 0.12)
    )
    do = max(1.2, min(sat * 1.35, do))

    ph = 7.3 + 0.42 * solar - 0.1 * (1 - solar) + rng.uniform(-0.03, 0.03)
    turbidity = 17 + 5 * math.sin(2 * math.pi * hour / 24 + 1.2) + rng.uniform(-1.2, 1.2)

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dissolved_oxygen": round(do, 2),
        "water_temperature": round(temp, 2),
        "ph": round(max(6.4, min(9.0, ph)), 2),
        "turbidity": round(max(4.0, min(80.0, turbidity)), 1),
        "saturation": round(sat, 2),
        "solar_factor": round(solar, 3),
    }


def record(pond: dict) -> dict:
    reading = sample(pond)
    previous = db.latest_reading(pond["id"])
    reading["quality"] = quality_flag(reading, previous)
    if reading["quality"] in ("ok", "spike"):
        db.insert_reading(pond["id"], reading, source="synthetic")
    return reading
