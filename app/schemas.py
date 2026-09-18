"""API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PondCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    latitude: float = Field(8.8932, ge=-90, le=90)
    longitude: float = Field(76.6141, ge=-180, le=180)
    length_m: float = Field(40.0, gt=1, le=1000)
    width_m: float = Field(25.0, gt=1, le=1000)
    depth_m: float = Field(2.0, gt=0.3, le=8)
    species: str = "tilapia"
    stock_count: int = Field(2000, ge=0, le=5_000_000)
    avg_weight_g: float = Field(120.0, gt=0, le=20000)
    aerator_count: int = Field(1, ge=0, le=20)
    aerator_kw: float = Field(1.5, ge=0, le=50)
    power_cost: float = Field(7.5, ge=0, le=100)


class PondUpdate(BaseModel):
    name: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    length_m: float | None = Field(None, gt=1, le=1000)
    width_m: float | None = Field(None, gt=1, le=1000)
    depth_m: float | None = Field(None, gt=0.3, le=8)
    species: str | None = None
    stock_count: int | None = Field(None, ge=0, le=5_000_000)
    avg_weight_g: float | None = Field(None, gt=0, le=20000)
    aerator_count: int | None = Field(None, ge=0, le=20)
    aerator_kw: float | None = Field(None, ge=0, le=50)
    power_cost: float | None = Field(None, ge=0, le=100)


class ForecastRequest(BaseModel):
    horizon_hours: int = Field(12, ge=1, le=48)
    aerator_schedule: list[bool] | None = None
    optimise: bool = False
    feed_kg_per_day: float = Field(0.0, ge=0, le=5000)


class StateOverride(BaseModel):
    dissolved_oxygen: float | None = Field(None, ge=0, le=25)
    water_temperature: float | None = Field(None, ge=5, le=45)
    ph: float | None = Field(None, ge=4, le=11)
    turbidity: float | None = Field(None, ge=0, le=400)
    alkalinity: float | None = Field(None, ge=5, le=500)


class ExperimentRequest(BaseModel):
    """Sandbox run. Nothing here is written back to the pond record."""

    pond_id: str | None = None
    horizon_hours: int = Field(18, ge=1, le=48)

    length_m: float = Field(40.0, gt=1, le=1000)
    width_m: float = Field(25.0, gt=1, le=1000)
    depth_m: float = Field(2.0, gt=0.3, le=8)
    species: str = "tilapia"
    stock_count: int = Field(2000, ge=0, le=5_000_000)
    avg_weight_g: float = Field(120.0, gt=0, le=20000)
    aerator_count: int = Field(1, ge=0, le=20)
    aerator_kw: float = Field(1.5, ge=0, le=50)
    power_cost: float = Field(7.5, ge=0, le=100)

    latitude: float = Field(8.8932, ge=-90, le=90)
    longitude: float = Field(76.6141, ge=-180, le=180)

    initial: StateOverride = StateOverride()
    aerator_schedule: list[bool] | None = None
    optimise: bool = False
    feed_kg_per_day: float = Field(0.0, ge=0, le=5000)
    use_live_weather: bool = True
