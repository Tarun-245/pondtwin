"""Aquaculture Pond Digital Twin - API and UI host."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, telemetry, weather
from .config import BASE_DIR, settings
from .engine import (
    SPECIES,
    EngineOptions,
    PhysicsEngine,
    PondConfig,
    WaterState,
    build_profile,
    do_saturation,
    recommend_schedule,
    species_thresholds,
    stratification_strength,
)
from .schemas import ExperimentRequest, ForecastRequest, PondCreate, PondUpdate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("pondtwin")

WEB_DIR = BASE_DIR / "web"


# --------------------------------------------------------------- lifecycle

async def telemetry_loop() -> None:
    """Sample every pond on a fixed cadence so history accumulates whether or
    not anyone has a browser open."""
    while True:
        try:
            for pond in db.list_ponds():
                telemetry.record(pond)
        except Exception:                                   # noqa: BLE001
            log.exception("telemetry loop failed")
        await asyncio.sleep(settings.telemetry_interval_seconds)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not db.list_ponds():
        db.create_pond({
            "name": "Grow-out pond 1",
            "latitude": 8.8932, "longitude": 76.6141,
            "length_m": 40.0, "width_m": 25.0, "depth_m": 2.2,
            "species": "tilapia", "stock_count": 2400, "avg_weight_g": 140.0,
            "aerator_count": 1, "aerator_kw": 1.5, "power_cost": 7.5,
        })
        log.info("seeded first pond")
    task = asyncio.create_task(telemetry_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

API = "/api/v1"


# ------------------------------------------------------------------ health

@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": settings.version}


@app.get("/readyz")
def readyz():
    try:
        db.list_ponds()
    except Exception as exc:                                # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {exc}") from exc
    return {"status": "ready"}


@app.get(f"{API}/species")
def species():
    return [{"key": k, **v} for k, v in SPECIES.items()]


# ------------------------------------------------------------------- ponds

@app.get(f"{API}/ponds")
def list_ponds():
    return db.list_ponds()


@app.post(f"{API}/ponds", status_code=201)
def create_pond(payload: PondCreate):
    pond = db.create_pond(payload.model_dump())
    telemetry.record(pond)
    return pond


@app.get(f"{API}/ponds/{{pond_id}}")
def get_pond(pond_id: str):
    pond = db.get_pond(pond_id)
    if not pond:
        raise HTTPException(404, "pond not found")
    return pond


@app.patch(f"{API}/ponds/{{pond_id}}")
def update_pond(pond_id: str, payload: PondUpdate):
    if not db.get_pond(pond_id):
        raise HTTPException(404, "pond not found")
    return db.update_pond(pond_id, payload.model_dump(exclude_unset=True))


@app.delete(f"{API}/ponds/{{pond_id}}", status_code=204)
def delete_pond(pond_id: str):
    if not db.delete_pond(pond_id):
        raise HTTPException(404, "pond not found")


# -------------------------------------------------------------- live state

def _config_from_pond(pond: dict) -> PondConfig:
    return PondConfig(
        length_m=pond["length_m"],
        width_m=pond["width_m"],
        depth_m=pond["depth_m"],
        species=pond["species"],
        stock_count=pond["stock_count"],
        avg_weight_g=pond["avg_weight_g"],
        aerator_count=pond["aerator_count"],
        aerator_kw=pond["aerator_kw"],
        power_cost=pond["power_cost"],
    )


@app.get(f"{API}/ponds/{{pond_id}}/state")
async def pond_state(pond_id: str):
    """What the pond is doing right now, resolved through the water column."""
    pond = db.get_pond(pond_id)
    if not pond:
        raise HTTPException(404, "pond not found")

    reading = telemetry.record(pond)
    hours, source = await weather.resolve(pond["latitude"], pond["longitude"], 1)
    w = hours[0]

    solar_norm = min(1.0, max(0.0, w.solar_radiation / 850.0))
    strength = stratification_strength(
        solar_norm, w.wind_speed_ms, pond["depth_m"], reading["turbidity"], False
    )
    layers = build_profile(reading["dissolved_oxygen"], strength, pond["depth_m"])
    thresholds = species_thresholds(pond["species"])
    min_do = min(l["dissolved_oxygen"] for l in layers)

    if min_do < thresholds["lethal"]:
        risk = "lethal"
    elif min_do < thresholds["critical"]:
        risk = "critical"
    elif min_do < thresholds["warning"]:
        risk = "warning"
    else:
        risk = "safe"

    cfg = _config_from_pond(pond)

    return {
        "pond": pond,
        "observed_at": reading["ts"],
        "weather_source": source,
        "reading": reading,
        "weather": {
            "time": w.time,
            "air_temperature": w.air_temperature,
            "wind_speed_ms": w.wind_speed_ms,
            "solar_radiation": w.solar_radiation,
            "relative_humidity": w.relative_humidity,
            "cloud_cover": w.cloud_cover,
        },
        "saturation": round(do_saturation(reading["water_temperature"]), 2),
        "stratification": round(strength, 2),
        "layers": layers,
        "min_do": round(min_do, 2),
        "risk": risk,
        "thresholds": thresholds,
        "volume_m3": round(cfg.volume_m3, 1),
        "biomass_kg": round(cfg.biomass_kg, 1),
        "density_kg_m3": round(cfg.density_kg_m3, 3),
    }


@app.get(f"{API}/ponds/{{pond_id}}/history")
def pond_history(pond_id: str, limit: int = 288):
    if not db.get_pond(pond_id):
        raise HTTPException(404, "pond not found")
    return db.reading_history(pond_id, min(limit, 2000))


# ---------------------------------------------------------------- forecast

def _initial_state(reading: dict, override=None) -> WaterState:
    state = WaterState(
        dissolved_oxygen=reading["dissolved_oxygen"],
        water_temperature=reading["water_temperature"],
        ph=reading["ph"],
        turbidity=reading["turbidity"],
    )
    if override:
        for key, value in override.model_dump(exclude_none=True).items():
            setattr(state, key, value)
    return state


@app.post(f"{API}/ponds/{{pond_id}}/forecast")
async def forecast(pond_id: str, payload: ForecastRequest):
    """The prediction for the real pond, from its real current state."""
    pond = db.get_pond(pond_id)
    if not pond:
        raise HTTPException(404, "pond not found")

    reading = db.latest_reading(pond["id"]) or telemetry.record(pond)
    hours, source = await weather.resolve(
        pond["latitude"], pond["longitude"], payload.horizon_hours
    )

    cfg = _config_from_pond(pond)
    initial = _initial_state(reading)
    options = EngineOptions(
        aerator_schedule=payload.aerator_schedule or [False] * payload.horizon_hours,
        feed_kg_per_day=payload.feed_kg_per_day,
    )

    if payload.optimise:
        result = recommend_schedule(cfg, initial, hours, options)
    else:
        result = PhysicsEngine().run(cfg, initial, hours, options)

    result["weather_source"] = source
    result["issued_at"] = datetime.now(timezone.utc).isoformat()
    result["initial"] = initial.__dict__
    result["pond_id"] = pond_id

    db.save_forecast(pond_id, payload.horizon_hours, result["engine"], result)
    return result


# -------------------------------------------------------------- experiment

@app.post(f"{API}/experiment")
async def experiment(payload: ExperimentRequest):
    """Sandbox.

    Takes an arbitrary pond configuration and starting state, runs the same
    engine, and returns the result without touching any stored pond. The UI
    seeds it from the live pond so a farmer starts from reality and changes
    one thing at a time.
    """
    initial = WaterState()
    latitude, longitude = payload.latitude, payload.longitude

    if payload.pond_id:
        pond = db.get_pond(payload.pond_id)
        if not pond:
            raise HTTPException(404, "pond not found")
        reading = db.latest_reading(pond["id"]) or telemetry.record(pond)
        initial = _initial_state(reading)
        latitude, longitude = pond["latitude"], pond["longitude"]

    for key, value in payload.initial.model_dump(exclude_none=True).items():
        setattr(initial, key, value)

    if payload.use_live_weather:
        hours, source = await weather.resolve(latitude, longitude, payload.horizon_hours)
    else:
        hours = weather.to_hours(weather.synthetic(payload.horizon_hours))
        source = "synthetic"

    cfg = PondConfig(
        length_m=payload.length_m,
        width_m=payload.width_m,
        depth_m=payload.depth_m,
        species=payload.species,
        stock_count=payload.stock_count,
        avg_weight_g=payload.avg_weight_g,
        aerator_count=payload.aerator_count,
        aerator_kw=payload.aerator_kw,
        power_cost=payload.power_cost,
    )
    options = EngineOptions(
        aerator_schedule=payload.aerator_schedule or [False] * payload.horizon_hours,
        feed_kg_per_day=payload.feed_kg_per_day,
    )

    if payload.optimise:
        result = recommend_schedule(cfg, initial, hours, options)
    else:
        result = PhysicsEngine().run(cfg, initial, hours, options)

    result["weather_source"] = source
    result["sandbox"] = True
    result["initial"] = initial.__dict__
    result["volume_m3"] = round(cfg.volume_m3, 1)
    result["biomass_kg"] = round(cfg.biomass_kg, 1)
    result["density_kg_m3"] = round(cfg.density_kg_m3, 3)
    return result


# ----------------------------------------------------------------- weather

@app.get(f"{API}/weather")
async def get_weather(latitude: float = 8.8932, longitude: float = 76.6141, hours: int = 24):
    try:
        return await weather.forecast(latitude, longitude, hours)
    except weather.WeatherUnavailable as exc:
        raise HTTPException(503, f"weather upstream unavailable: {exc}") from exc


# ---------------------------------------------------------------------- UI

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
