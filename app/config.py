"""Application settings. Everything configurable lives here, not in source."""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "Aquaculture Pond Digital Twin"
    version: str = "2.0.0"

    # Storage
    database_path: str = str(BASE_DIR / "data" / "pondtwin.db")

    # CORS: list real origins, never "*" with credentials
    cors_origins: list[str] = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Weather upstream
    weather_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_cache_seconds: int = 900
    weather_timeout_seconds: float = 10.0
    weather_retries: int = 2

    # Telemetry sampling loop (seconds). Replace with real ingestion later.
    telemetry_interval_seconds: int = 60

    # Simulation
    layers: int = 12
    substeps_per_hour: int = 4
    max_horizon_hours: int = 48

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PONDTWIN_",
        extra="ignore",
    )


settings = Settings()
