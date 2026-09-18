"""SQLite persistence.

Two things live here that the old version had none of:
  * ponds      - a farmer can own many, each with its own geometry and stock
  * readings   - every telemetry sample is kept, in UTC

The readings table is the foundation for model training later. Nothing can be
learned from data that was never written down.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS ponds (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    length_m        REAL NOT NULL,
    width_m         REAL NOT NULL,
    depth_m         REAL NOT NULL,
    species         TEXT NOT NULL DEFAULT 'tilapia',
    stock_count     INTEGER NOT NULL DEFAULT 2000,
    avg_weight_g    REAL NOT NULL DEFAULT 120,
    aerator_count   INTEGER NOT NULL DEFAULT 1,
    aerator_kw      REAL NOT NULL DEFAULT 1.5,
    power_cost      REAL NOT NULL DEFAULT 7.5,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pond_id         TEXT NOT NULL,
    ts              TEXT NOT NULL,
    dissolved_oxygen REAL,
    water_temperature REAL,
    ph              REAL,
    turbidity       REAL,
    quality         TEXT NOT NULL DEFAULT 'ok',
    source          TEXT NOT NULL DEFAULT 'synthetic',
    FOREIGN KEY (pond_id) REFERENCES ponds(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_readings_pond_ts ON readings(pond_id, ts DESC);

CREATE TABLE IF NOT EXISTS forecasts (
    id              TEXT PRIMARY KEY,
    pond_id         TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    horizon_hours   INTEGER NOT NULL,
    engine          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    FOREIGN KEY (pond_id) REFERENCES ponds(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_forecasts_pond ON forecasts(pond_id, issued_at DESC);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def connect():
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ----------------------------------------------------------------- ponds

POND_FIELDS = (
    "name", "latitude", "longitude", "length_m", "width_m", "depth_m",
    "species", "stock_count", "avg_weight_g", "aerator_count",
    "aerator_kw", "power_cost",
)


def create_pond(data: dict) -> dict:
    pond_id = new_id()
    with connect() as conn:
        conn.execute(
            f"INSERT INTO ponds (id, {', '.join(POND_FIELDS)}, created_at) "
            f"VALUES (?, {', '.join('?' * len(POND_FIELDS))}, ?)",
            [pond_id] + [data[f] for f in POND_FIELDS] + [now_utc()],
        )
    return get_pond(pond_id)


def list_ponds() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ponds ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def get_pond(pond_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM ponds WHERE id = ?", (pond_id,)).fetchone()
    return dict(row) if row else None


def update_pond(pond_id: str, data: dict) -> dict | None:
    fields = {k: v for k, v in data.items() if k in POND_FIELDS and v is not None}
    if not fields:
        return get_pond(pond_id)
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE ponds SET {assignments} WHERE id = ?",
            list(fields.values()) + [pond_id],
        )
    return get_pond(pond_id)


def delete_pond(pond_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM ponds WHERE id = ?", (pond_id,))
    return cur.rowcount > 0


# -------------------------------------------------------------- readings

def insert_reading(pond_id: str, reading: dict, source: str = "synthetic") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO readings (pond_id, ts, dissolved_oxygen, water_temperature,"
            " ph, turbidity, quality, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pond_id,
                reading["ts"],
                reading.get("dissolved_oxygen"),
                reading.get("water_temperature"),
                reading.get("ph"),
                reading.get("turbidity"),
                reading.get("quality", "ok"),
                source,
            ),
        )


def latest_reading(pond_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM readings WHERE pond_id = ? ORDER BY ts DESC LIMIT 1",
            (pond_id,),
        ).fetchone()
    return dict(row) if row else None


def reading_history(pond_id: str, limit: int = 288) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM readings WHERE pond_id = ? ORDER BY ts DESC LIMIT ?",
            (pond_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ------------------------------------------------------------- forecasts

def save_forecast(pond_id: str, horizon: int, engine: str, payload: dict) -> str:
    fid = new_id()
    with connect() as conn:
        conn.execute(
            "INSERT INTO forecasts (id, pond_id, issued_at, horizon_hours, engine, payload)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (fid, pond_id, now_utc(), horizon, engine, json.dumps(payload)),
        )
    return fid
