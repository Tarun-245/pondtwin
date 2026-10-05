from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException

from app import telemetry

MAPPING = {"water_temperature": 2, "ph": 4, "dissolved_oxygen": 1, "turbidity": 3}


class MemoryStore:
    def __init__(self):
        self.config = {"channel_id": 123, "field_map": MAPPING,
                       "read_api_key": "testreadkey", "synced_at": None}
        self.rows = {}

    def connection(self, pond_id):
        return self.config

    def latest_reading(self, pond_id):
        return max(self.rows.values(), key=lambda r: r["ts"]) if self.rows else None

    def insert_readings(self, pond_id, rows):
        for row in rows:
            self.rows.setdefault(row["entry_id"], row)

    def mark_synced(self, pond_id):
        self.config["synced_at"] = datetime.now(timezone.utc).isoformat()


def mock_client(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(telemetry.httpx, "Client", lambda **kwargs:
                        original(transport=httpx.MockTransport(handler), **kwargs))


def test_real_ingestion_path_is_readonly_and_reuses_cached_entries(monkeypatch):
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.thingspeak.com"
        assert request.url.params["api_key"] == "testreadkey"
        return httpx.Response(200, json={"feeds": [
            {"created_at": datetime.now(timezone.utc).isoformat(), "entry_id": 5,
             "field1": "6", "field2": "28", "field3": "20", "field4": "7.5"}]})
    mock_client(monkeypatch, upstream)
    store = MemoryStore()
    first = telemetry.record({"id": "pond"}, store)
    second = telemetry.record({"id": "pond"}, store)
    assert first["entry_id"] == second["entry_id"] == 5
    assert len(requests) == len(store.rows) == 1
    assert first["water_temperature"] == 28
    assert first["source"] == "thingspeak"


def test_upstream_failure_does_not_create_a_simulated_reading(monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(404))
    store = MemoryStore()
    with pytest.raises(HTTPException) as err:
        telemetry.record({"id": "pond"}, store)
    assert err.value.status_code == 502
    assert "testreadkey" not in err.value.detail
    assert store.rows == {}


def test_missing_sensor_value_is_retained_as_invalid_and_not_used(monkeypatch):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"feeds": [
        {"created_at": datetime.now(timezone.utc).isoformat(), "entry_id": 5,
         "field1": "6", "field2": None, "field3": "20", "field4": "7.5"}]}))
    store = MemoryStore()
    with pytest.raises(HTTPException) as err:
        telemetry.record({"id": "pond"}, store)
    assert err.value.status_code == 409
    assert store.rows[5]["quality"] == "missing"
    assert store.rows[5]["water_temperature"] is None
