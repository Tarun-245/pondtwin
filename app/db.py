"""Request-scoped Supabase persistence. Every call uses the farmer's JWT."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

from .config import settings


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def checked_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(404, "Pond not found.") from exc


def public_config() -> dict:
    url = settings.supabase_url.rstrip("/")
    key = settings.supabase_publishable_key
    # Never expose a privileged key via this public configuration endpoint.
    if not url.startswith("https://") or not key.startswith("sb_publishable_"):
        raise HTTPException(503, "PondTwin account setup is not complete.")
    return {"supabase_url": url, "supabase_publishable_key": key}


class Store:
    def __init__(self, token: str):
        cfg = public_config()
        self.client = httpx.Client(
            base_url=cfg["supabase_url"],
            headers={"apikey": cfg["supabase_publishable_key"],
                     "Authorization": f"Bearer {token}"},
            timeout=settings.upstream_timeout_seconds,
            follow_redirects=False,
        )
        try:
            user = self.request("GET", "/auth/v1/user", auth=True)
            self.owner_id = str(UUID(user["id"]))
            if user.get("is_anonymous"):
                raise HTTPException(401, "Please sign in with your farmer account.")
            self.user = {"id": self.owner_id, "email": user.get("email", "")}
        except Exception:
            self.close()
            raise

    def close(self):
        self.client.close()

    def request(self, method, path, *, auth=False, **kwargs):
        try:
            res = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Account storage is temporarily unavailable.") from exc
        if res.status_code in (401, 403):
            raise HTTPException(401 if auth else 403,
                                "Please sign in again." if auth else "This operation is not allowed.")
        if not res.is_success:
            raise HTTPException(503, "Account storage is not ready. Check the database setup.")
        if not res.content:
            return None
        try:
            return res.json()
        except ValueError as exc:
            raise HTTPException(503, "Account storage returned an invalid response.") from exc

    def rest(self, method, table, *, params=None, data=None, prefer=None):
        kwargs = {"params": params or {}}
        if data is not None:
            kwargs["json"] = data
        if prefer:
            kwargs["headers"] = {"Prefer": prefer}
        return self.request(method, f"/rest/v1/{table}", **kwargs)

    def list_ponds(self):
        return self.rest("GET", "ponds", params={"owner_id": f"eq.{self.owner_id}",
                                                   "order": "created_at.asc"})

    def get_pond(self, pond_id):
        rows = self.rest("GET", "ponds", params={"id": f"eq.{checked_id(pond_id)}",
                       "owner_id": f"eq.{self.owner_id}", "limit": "1"})
        return rows[0] if rows else None

    def create_pond(self, data):
        data = {**data, "id": str(uuid4()), "owner_id": self.owner_id}
        return self.rest("POST", "ponds", data=data, prefer="return=representation")[0]

    def update_pond(self, pond_id, data):
        if not data:
            return self.get_pond(pond_id)
        rows = self.rest("PATCH", "ponds", params={"id": f"eq.{checked_id(pond_id)}",
                     "owner_id": f"eq.{self.owner_id}"}, data=data, prefer="return=representation")
        return rows[0] if rows else None

    def delete_pond(self, pond_id):
        return bool(self.rest("DELETE", "ponds", params={"id": f"eq.{checked_id(pond_id)}",
                         "owner_id": f"eq.{self.owner_id}"}, prefer="return=representation"))

    def connection(self, pond_id):
        rows = self.rest("GET", "pond_connections", params={
            "pond_id": f"eq.{checked_id(pond_id)}", "owner_id": f"eq.{self.owner_id}", "limit": "1"})
        return rows[0] if rows else None

    def save_connection(self, pond_id, data):
        previous = self.connection(pond_id)
        if data.get("read_api_key") is None:
            data["read_api_key"] = (previous or {}).get("read_api_key", "")
        data.update(pond_id=checked_id(pond_id), owner_id=self.owner_id, synced_at=None)
        return self.rest("POST", "pond_connections", params={"on_conflict": "pond_id"}, data=data,
                         prefer="resolution=merge-duplicates,return=representation")[0]

    def mark_synced(self, pond_id):
        self.rest("PATCH", "pond_connections", params={"pond_id": f"eq.{checked_id(pond_id)}",
                  "owner_id": f"eq.{self.owner_id}"}, data={"synced_at": now_utc()})

    def insert_readings(self, pond_id, readings):
        if not readings:
            return
        data = [{**r, "pond_id": checked_id(pond_id), "owner_id": self.owner_id} for r in readings]
        self.rest("POST", "readings", params={"on_conflict": "pond_id,entry_id"}, data=data,
                  prefer="resolution=ignore-duplicates")

    def latest_reading(self, pond_id):
        rows = self.rest("GET", "readings", params={"pond_id": f"eq.{checked_id(pond_id)}",
                        "owner_id": f"eq.{self.owner_id}", "order": "ts.desc,entry_id.desc", "limit": "1"})
        return rows[0] if rows else None

    def reading_history(self, pond_id, limit=288):
        rows = self.rest("GET", "readings", params={"pond_id": f"eq.{checked_id(pond_id)}",
                  "owner_id": f"eq.{self.owner_id}", "order": "ts.desc,entry_id.desc", "limit": str(limit)})
        return list(reversed(rows))

    def save_forecast(self, pond_id, horizon, engine, payload):
        fid = str(uuid4())
        self.rest("POST", "forecasts", data={"id": fid, "pond_id": checked_id(pond_id),
                  "owner_id": self.owner_id, "horizon_hours": horizon, "engine": engine, "payload": payload})
        return fid
