"""Device onboarding uses actual labels/assigned configuration, never field guesses."""
import json

import pytest
from fastapi import HTTPException

from app.devices import infer_fields
from app import weather
from datetime import datetime, timezone
from tests.test_backend import backend, FIELD_MAP, OWNER, POND

AUTH = {"Authorization": "Bearer valid"}


def test_assigned_device_list_never_exposes_read_key(backend):
    client, state, requests = backend
    state["device"] = {"channel_id": 123, "owner_id": OWNER, "label": "Farm sensor",
                       "read_api_key": "PRIVATEDEVICEKEY", "field_map": FIELD_MAP}
    result = client.get("/api/v1/devices", headers=AUTH)
    assert result.status_code == 200
    assert result.json() == [{"channel_id": 123, "label": "Farm sensor", "field_map": FIELD_MAP}]
    assert "PRIVATEDEVICEKEY" not in result.text
    query = next(r for r in requests if r.url.path == "/rest/v1/provisioned_devices")
    assert query.url.params["select"] == "channel_id,label,field_map"


def test_channel_id_alone_uses_labels_and_one_atomic_write(backend):
    client, _, requests = backend
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Farm pond", "connection": {"channel_id": 123}})
    assert result.status_code == 201
    writes = [r for r in requests if r.url.host == "test.supabase.co" and r.method != "GET"]
    assert len(writes) == 1
    assert writes[0].url.path == "/rest/v1/rpc/save_pond"
    assert json.loads(writes[0].content)["p_connection"] == {
        "channel_id": 123, "read_api_key": "", "field_map": FIELD_MAP}
    assert "connection" not in result.json()


def test_assigned_private_device_needs_no_farmer_key_or_mapping(backend):
    client, state, requests = backend
    state["device"] = {"channel_id": 123, "owner_id": OWNER,
                       "read_api_key": "DEVICEPRIVATEKEY", "field_map": FIELD_MAP}
    state["channel"] = {f"field{n}": f"Sensor {n}" for n in range(1, 5)}
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Assigned pond", "connection": {"channel_id": 123}})
    assert result.status_code == 201
    upstream = [r for r in requests if r.url.host == "api.thingspeak.com"]
    assert upstream[0].url.params["api_key"] == "DEVICEPRIVATEKEY"
    assert "DEVICEPRIVATEKEY" not in result.text


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_unreadable_channel_never_leaves_a_partial_pond(backend, status):
    client, state, requests = backend
    state["channel_status"] = status
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Retry pond", "connection": {"channel_id": 123}})
    assert result.status_code == (422 if status < 429 else 502)
    assert not any(r.method != "GET" and r.url.host == "test.supabase.co" for r in requests)


def test_unknown_labels_show_advanced_setup_without_writes(backend):
    client, state, requests = backend
    state["channel"] = {f"field{n}": f"Field {n}" for n in range(1, 5)}
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Unconfigured", "connection": {"channel_id": 123}})
    assert result.status_code == 422
    assert "Advanced sensor setup" in result.json()["detail"]
    assert not any(r.method != "GET" and r.url.host == "test.supabase.co" for r in requests)


def test_explicit_mapping_can_connect_unlabelled_channel(backend):
    client, state, _ = backend
    state["channel"] = {f"field{n}": f"Sensor {n}" for n in range(1, 5)}
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Manual", "connection": {"channel_id": 123, "field_map": FIELD_MAP}})
    assert result.status_code == 201


def test_edit_with_channel_id_preserves_existing_private_key(backend):
    client, state, requests = backend
    state["connection"] = {"channel_id": 123, "read_api_key": "SAVEDKEY", "field_map": FIELD_MAP}
    result = client.patch(f"/api/v1/ponds/{POND}", headers=AUTH,
                          json={"name": "Renamed", "connection": {"channel_id": 123}})
    assert result.status_code == 200
    write = next(r for r in requests if r.url.path == "/rest/v1/rpc/save_pond")
    assert json.loads(write.content)["p_connection"]["read_api_key"] == "SAVEDKEY"
    assert "SAVEDKEY" not in result.text


def test_new_channel_cannot_reuse_previous_channel_private_key(backend):
    client, state, requests = backend
    state["connection"] = {"channel_id": 123, "read_api_key": "PREVIOUSKEY", "field_map": FIELD_MAP}
    state["reading"] = None
    result = client.patch(f"/api/v1/ponds/{POND}", headers=AUTH,
                          json={"connection": {"channel_id": 999}})
    assert result.status_code == 200
    upstream = next(r for r in requests if r.url.host == "api.thingspeak.com")
    assert "api_key" not in upstream.url.params


def test_duplicate_temperature_labels_are_not_guessed():
    with pytest.raises(HTTPException) as exc:
        infer_fields({"id": 1, "field1": "Temperature", "field2": "Water Temperature",
                      "field3": "pH", "field4": "DO", "field5": "Turbidity"})
    assert exc.value.status_code == 422


def test_missing_enabled_field_is_rejected(backend):
    client, state, _ = backend
    del state["channel"]["field4"]
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Bad mapping", "connection": {"channel_id": 123, "field_map": FIELD_MAP}})
    assert result.status_code == 422


def test_actual_voltage_channel_uses_only_supported_measurements(backend):
    client, state, requests = backend
    state["channel"] = {"field1": "Temparature (C)", "field2": "pH",
                        "field3": "Turbidity Sensor Voltage (V)", "field4": "Filter State"}
    result = client.post("/api/v1/ponds", headers=AUTH,
                         json={"name": "Device pond", "connection": {"channel_id": 3449300}})
    assert result.status_code == 201
    write = next(r for r in requests if r.url.path == "/rest/v1/rpc/save_pond")
    assert json.loads(write.content)["p_connection"]["field_map"] == {
        "water_temperature": 1, "ph": 2, "turbidity_voltage": 3}


def test_partial_state_shows_readings_without_fabricated_oxygen(backend, monkeypatch):
    client, state, _ = backend
    state["connection"] = {"channel_id": 3449300, "field_map": {"water_temperature": 1, "ph": 2, "turbidity_voltage": 3},
                           "synced_at": datetime.now(timezone.utc).isoformat()}
    state["reading"].update(dissolved_oxygen=None, turbidity=None, turbidity_voltage=1.4, quality="missing")
    async def resolved(lat, lon, hours):
        return weather.to_hours(weather.synthetic(hours)), "synthetic"
    monkeypatch.setattr(weather, "resolve", resolved)
    result = client.get(f"/api/v1/ponds/{POND}/state", headers=AUTH)
    assert result.status_code == 200
    data = result.json()
    assert data["reading"]["dissolved_oxygen"] is None
    assert data["reading"]["turbidity"] is None
    assert data["reading"]["turbidity_voltage"] == 1.4
    assert data["layers"] == []
    assert data["risk"] == "unknown"
    assert data["forecast_available"] is False
    assert "dissolved oxygen" in data["forecast_block_reason"]
    forecast = client.post(f"/api/v1/ponds/{POND}/forecast", headers=AUTH, json={})
    assert forecast.status_code == 409
