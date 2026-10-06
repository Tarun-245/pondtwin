"""Backend integration tests with mocked upstreams; no live credentials."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, telemetry, weather
from app.config import settings
from app.main import app
from app.schemas import ThingSpeakConfig

OWNER = str(uuid4())
OTHER_OWNER = str(uuid4())
POND = str(uuid4())
OTHER_POND = str(uuid4())
FIELD_MAP = {"water_temperature": 2, "ph": 4, "dissolved_oxygen": 1, "turbidity": 3}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://test.supabase.co")
    monkeypatch.setattr(settings, "supabase_publishable_key", "sb_publishable_test")
    requests = []
    reading = {"ts": datetime.now(timezone.utc).isoformat(), "entry_id": 1,
               "quality": "ok", "source": "thingspeak", "dissolved_oxygen": 6.0,
               "water_temperature": 28.0, "ph": 7.4, "turbidity": 20.0}
    pond = {"id": POND, "owner_id": OWNER, "name": "Private pond", "length_m": 40.0,
            "width_m": 25.0, "depth_m": 2.0, "species": "tilapia", "stock_count": 2000,
            "avg_weight_g": 120, "aerator_count": 1, "aerator_kw": 1.5,
            "power_cost": 7.5, "latitude": 8.9, "longitude": 76.6}
    state = {"reading": reading, "pond": pond, "connection": None, "device": None,
             "channel_status": 200,
             "channel": {"field1": "Dissolved Oxygen (mg/L)", "field2": "Water Temperature (C)",
                         "field3": "Turbidity (NTU)", "field4": "pH"}}

    def upstream(request):
        requests.append(request)
        if request.url.host == "api.thingspeak.com":
            assert request.method == "GET"
            channel = {"id": int(request.url.path.split("/")[2]), **state["channel"]}
            return httpx.Response(state["channel_status"], json={"channel": channel, "feeds": []})
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("authorization")
            if token == "Bearer valid":
                return httpx.Response(200, json={"id": OWNER, "email": "farmer@example.com"})
            if token == "Bearer anonymous":
                return httpx.Response(200, json={"id": OWNER, "is_anonymous": True})
            return httpx.Response(401, json={"message": "invalid token"})
        assert request.headers["authorization"] == "Bearer valid"
        assert request.headers["apikey"] == "sb_publishable_test"
        if request.url.path == "/rest/v1/provisioned_devices":
            assert request.url.params["owner_id"] == f"eq.{OWNER}"
            device = state["device"]
            channel_filter = request.url.params.get("channel_id")
            matches = device and channel_filter in (None, f"eq.{device['channel_id']}")
            return httpx.Response(200, json=[device] if matches else [])
        if request.url.path == "/rest/v1/rpc/save_pond":
            data = json.loads(request.content)
            return httpx.Response(200, json={**data["p_pond"], "id": data["p_pond_id"] or str(uuid4()), "owner_id": OWNER})
        if request.url.path == "/rest/v1/ponds":
            if request.method == "GET":
                assert request.url.params["owner_id"] == f"eq.{OWNER}"
                target = request.url.params.get("id")
                return httpx.Response(200, json=[pond] if target in (None, f"eq.{POND}") else [])
            data = json.loads(request.content) if request.content else None
            if request.method == "POST":
                assert data["owner_id"] == OWNER
                return httpx.Response(201, json=[data])
            return httpx.Response(200, json=[pond])
        if request.url.path == "/rest/v1/pond_connections":
            if request.method == "GET":
                return httpx.Response(200, json=[state["connection"]] if state["connection"] else [])
            data = json.loads(request.content)
            return httpx.Response(200, json=[data]) if request.method == "POST" else httpx.Response(204)
        if request.url.path == "/rest/v1/readings":
            return httpx.Response(200, json=[state["reading"]]) if request.method == "GET" else httpx.Response(201)
        if request.url.path == "/rest/v1/forecasts":
            payload = json.loads(request.content)
            assert payload["owner_id"] == OWNER
            assert payload["pond_id"] == POND
            return httpx.Response(201)
        raise AssertionError(f"Unexpected upstream path {request.url.path}")

    original_client = httpx.Client
    def mocked_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(upstream)
        return original_client(*args, **kwargs)
    monkeypatch.setattr(db.httpx, "Client", mocked_client)
    with TestClient(app) as client:
        yield client, state, requests


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/v1/devices", None),
    ("GET", "/api/v1/ponds", None),
    ("POST", "/api/v1/ponds", {"name": "pond"}),
    ("GET", f"/api/v1/ponds/{POND}", None),
    ("PATCH", f"/api/v1/ponds/{POND}", {"name": "edit"}),
    ("DELETE", f"/api/v1/ponds/{POND}", None),
    ("GET", f"/api/v1/ponds/{POND}/state", None),
    ("GET", f"/api/v1/ponds/{POND}/history", None),
    ("POST", f"/api/v1/ponds/{POND}/forecast", {}),
    ("GET", f"/api/v1/ponds/{POND}/connection", None),
    ("PUT", f"/api/v1/ponds/{POND}/connection", {"channel_id": 12, "field_map": FIELD_MAP}),
    ("POST", "/api/v1/experiment", {}),
    ("GET", "/api/v1/weather", None),
    ("GET", "/api/v1/me", None),
    ("GET", "/readyz", None),
])
def test_private_endpoints_require_signin(backend, method, path, payload):
    client, _, requests = backend
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert not requests


@pytest.mark.parametrize("token", ["invalid", "anonymous"])
def test_invalid_and_anonymous_sessions_rejected(backend, token):
    client, _, requests = backend
    res = client.get("/api/v1/ponds", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert all(r.url.path == "/auth/v1/user" for r in requests)


def test_valid_user_only_queries_own_ponds(backend):
    client, _, _ = backend
    res = client.get("/api/v1/ponds", headers={"Authorization": "Bearer valid"})
    assert res.status_code == 200
    assert [p["id"] for p in res.json()] == [POND]
    assert client.get(f"/api/v1/ponds/{OTHER_POND}",
                      headers={"Authorization": "Bearer valid"}).status_code == 404


def test_client_cannot_supply_owner(backend):
    client, _, _ = backend
    res = client.post("/api/v1/ponds", headers={"Authorization": "Bearer valid"},
                      json={"name": "pond", "owner_id": OTHER_OWNER})
    assert res.status_code == 422


def test_new_pond_has_no_simulated_reading(backend):
    client, _, requests = backend
    res = client.post("/api/v1/ponds", headers={"Authorization": "Bearer valid"}, json={"name": "New pond"})
    assert res.status_code == 201
    assert not any(r.url.path == "/rest/v1/readings" for r in requests)


def test_state_without_connection_reports_setup(backend):
    client, _, _ = backend
    res = client.get(f"/api/v1/ponds/{POND}/state", headers={"Authorization": "Bearer valid"})
    assert res.status_code == 409
    assert "ThingSpeak" in res.json()["detail"]


def test_stale_data_cannot_drive_current_forecast(backend):
    client, state, requests = backend
    state["connection"] = {"channel_id": 123, "field_map": FIELD_MAP,
                           "synced_at": datetime.now(timezone.utc).isoformat()}
    state["reading"]["ts"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    res = client.post(f"/api/v1/ponds/{POND}/forecast", headers={"Authorization": "Bearer valid"}, json={})
    assert res.status_code == 409
    assert not any(r.url.path == "/rest/v1/forecasts" for r in requests)


def test_physics_forecast_explicitly_labels_its_source(backend, monkeypatch):
    client, state, _ = backend
    state["connection"] = {"channel_id": 123, "field_map": FIELD_MAP,
                           "synced_at": datetime.now(timezone.utc).isoformat()}
    async def resolved(lat, lon, hours):
        return weather.to_hours(weather.synthetic(hours)), "synthetic"
    monkeypatch.setattr(weather, "resolve", resolved)
    res = client.post(f"/api/v1/ponds/{POND}/forecast", headers={"Authorization": "Bearer valid"},
                      json={"horizon_hours": 2})
    assert res.status_code == 200
    payload = res.json()
    assert payload["transformer_available"] is False
    assert payload["telemetry_source"] == "thingspeak"
    assert len(payload["results"]) == 2


def test_saved_read_key_never_returned(backend):
    client, state, _ = backend
    state["connection"] = {"channel_id": 123, "field_map": FIELD_MAP, "read_api_key": "privatekey"}
    res = client.get(f"/api/v1/ponds/{POND}/connection", headers={"Authorization": "Bearer valid"})
    assert res.status_code == 200
    assert res.json()["has_read_api_key"]
    assert "privatekey" not in res.text


def test_changing_sensor_series_cannot_mix_history(backend):
    client, state, _ = backend
    state["connection"] = {"channel_id": 123, "field_map": FIELD_MAP, "read_api_key": ""}
    res = client.put(f"/api/v1/ponds/{POND}/connection", headers={"Authorization": "Bearer valid"},
                     json={"channel_id": 999, "field_map": FIELD_MAP})
    assert res.status_code == 409


def test_privileged_key_cannot_leak(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://test.supabase.co")
    monkeypatch.setattr(settings, "supabase_publishable_key", "sb_secret_do_not_return")
    with TestClient(app) as client:
        res = client.get("/api/v1/public-config")
    assert res.status_code == 503
    assert "sb_secret" not in res.text


def test_mapping_is_explicit_and_timestamp_order_preserved():
    stamp = datetime.now(timezone.utc) - timedelta(minutes=1)
    feeds = [{"created_at": stamp.isoformat(), "entry_id": 2, "field1": "6.1",
              "field2": "28.2", "field3": "18.0", "field4": "7.6"},
             {"created_at": (stamp-timedelta(minutes=1)).isoformat(), "entry_id": 1,
              "field1": "6", "field2": "28", "field3": "18", "field4": "7.5"}]
    readings, skipped = telemetry.parse_feeds(feeds, FIELD_MAP)
    assert skipped == 0
    assert [r["entry_id"] for r in readings] == [1, 2]
    assert readings[-1]["water_temperature"] == 28.2
    assert readings[-1]["dissolved_oxygen"] == 6.1
    assert readings[-1]["ph"] == 7.6
    assert readings[-1]["source"] == "thingspeak"


@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "not-a-number"])
def test_bad_values_never_become_fake_measurements(value):
    feeds = [{"created_at": datetime.now(timezone.utc).isoformat(), "entry_id": 1,
              "field1": "6", "field2": value, "field3": "20", "field4": "7.5"}]
    readings, _ = telemetry.parse_feeds(feeds, FIELD_MAP)
    assert readings[0]["water_temperature"] is None
    assert readings[0]["quality"] == "missing"


def test_bad_timestamps_are_skipped():
    feeds = [{"created_at": "2026-01-01T00:00:00", "entry_id": 1},
             {"created_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), "entry_id": 2}]
    assert telemetry.parse_feeds(feeds, FIELD_MAP) == ([], 2)


def test_duplicate_field_mapping_is_rejected():
    with pytest.raises(ValueError):
        ThingSpeakConfig(channel_id=1, field_map={k: 1 for k in FIELD_MAP})
