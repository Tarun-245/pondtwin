"""Original-framework parity, preprocessing, input provenance and sampling."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi import HTTPException
import numpy as np
import pytest

from app import transformer


def reading(stamp, oxygen=6.0):
    return {"ts": stamp.isoformat(), "quality": "ok" if oxygen is not None else "missing",
            "water_temperature": 26.0, "ph": 7.44, "dissolved_oxygen": oxygen, "turbidity": 40.6}


def test_original_pytorch_prediction_parity():
    fixture = json.loads((Path(__file__).parent / "transformer_reference.json").read_text())
    result = transformer.get_model().forward_scaled(fixture["scaled_input"])
    np.testing.assert_allclose(result, fixture["scaled_prediction"], atol=2e-5, rtol=2e-5)


def test_scaler_feature_order_and_inverse():
    model = transformer.get_model()
    values = np.tile(model.mean, (12, 1))
    expected = model.forward_scaled(np.zeros((12, 4))) * model.scale + model.mean
    actual = model.predict(values)
    np.testing.assert_allclose(list(actual.values()), expected, atol=2e-5)
    assert list(actual) == list(transformer.KEYS)


@pytest.mark.parametrize("values", [np.zeros((11, 4)), np.zeros((12, 3)), np.full((12, 4), np.nan)])
def test_invalid_window_is_rejected(values):
    with pytest.raises(ValueError):
        transformer.get_model().forward_scaled(values)


def test_live_window_has_twelve_separate_recent_samples():
    now = datetime.now(timezone.utc)
    history = [reading(now - timedelta(minutes=20 * (11-i))) for i in range(12)]
    window, method = transformer.prepare_window(history, history[-1])
    assert len(window) == 12
    assert len({r["ts"] for r in window}) == 12
    assert method == "nearest_sensor_sample_within_10_minutes"


@pytest.mark.parametrize("case", ["stale", "missing", "insufficient"])
def test_live_model_never_fills_invalid_or_missing_history(case):
    now = datetime.now(timezone.utc)
    latest = reading(now - timedelta(hours=2) if case == "stale" else now,
                     None if case == "missing" else 6.0)
    with pytest.raises(HTTPException) as error:
        transformer.prepare_window([latest], latest)
    assert error.value.status_code == 409


def test_demo_generates_only_ephemeral_oxygen_with_original_timestamps():
    old = datetime(2025, 8, 16, 23, 55, tzinfo=timezone.utc)
    latest = reading(old, None)
    window, method = transformer.prepare_window([latest], latest, True)
    assert latest["dissolved_oxygen"] is None
    assert window[-1]["ts"] == latest["ts"]
    assert all(4.5 <= row["dissolved_oxygen"] <= 8.0 for row in window)
    assert method == "demo_history_seeded_from_available_snapshots"
    assert all(row["water_temperature"] == latest["water_temperature"] for row in window)
    prediction = transformer.get_model().predict([[row[key] for key in transformer.KEYS] for row in window])
    assert np.isfinite(list(prediction.values())).all()
