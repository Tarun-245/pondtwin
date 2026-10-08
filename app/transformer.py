"""TransformerV1 inference using exported, unchanged float32 checkpoint tensors.

The small NumPy runtime avoids loading PyTorch/CUDA on a free 512 MB service.
Export parity is checked against the notebook's PyTorch architecture.
"""
from datetime import timedelta
from functools import lru_cache
import json
import math
from pathlib import Path
import random

import numpy as np
from fastapi import HTTPException

from . import telemetry

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
KEYS = ("water_temperature", "ph", "dissolved_oxygen", "turbidity")


class PondTransformer:
    def __init__(self):
        self.config = json.loads((MODEL_DIR / "transformer_v1.json").read_text())
        with np.load(MODEL_DIR / "transformer_v1.npz", allow_pickle=False) as archive:
            self.weights = {key: archive[key] for key in archive.files}
        if not all(np.isfinite(value).all() for value in self.weights.values()):
            raise ValueError("Non-finite model weights.")
        self.mean = np.asarray(self.config["scaler_mean"], dtype=np.float64)
        self.scale = np.asarray(self.config["scaler_scale"], dtype=np.float64)

    def linear(self, x, prefix):
        return x @ self.weights[prefix + ".weight"].T + self.weights[prefix + ".bias"]

    def norm(self, x, prefix):
        centered = x - x.mean(axis=-1, keepdims=True)
        return centered / np.sqrt((centered ** 2).mean(axis=-1, keepdims=True) + 1e-5) * self.weights[prefix + ".weight"] + self.weights[prefix + ".bias"]

    @staticmethod
    def gelu(x):
        erf = np.fromiter((math.erf(float(v) / math.sqrt(2)) for v in x.flat),
                          dtype=np.float32, count=x.size).reshape(x.shape)
        return x * np.float32(0.5) * (np.float32(1) + erf)

    def forward_scaled(self, values):
        x = np.asarray(values, dtype=np.float32)
        if x.shape != (12, 4) or not np.isfinite(x).all():
            raise ValueError("Model needs a finite 12 by 4 window.")
        x = self.linear(x, "input_projection") + self.weights["positional_embedding"][0]
        for layer in range(2):
            prefix = f"transformer_encoder.layers.{layer}"
            normalized = self.norm(x, prefix + ".norm1")
            qkv = normalized @ self.weights[prefix + ".self_attn.in_proj_weight"].T + self.weights[prefix + ".self_attn.in_proj_bias"]
            q, k, v = [a.reshape(12, 4, 16).transpose(1, 0, 2) for a in np.split(qkv, 3, axis=-1)]
            scores = (q @ k.transpose(0, 2, 1)) / np.float32(4)
            probabilities = np.exp(scores - scores.max(axis=-1, keepdims=True))
            probabilities /= probabilities.sum(axis=-1, keepdims=True)
            attended = (probabilities @ v).transpose(1, 0, 2).reshape(12, 64)
            x = x + self.linear(attended, prefix + ".self_attn.out_proj")
            x = x + self.linear(self.gelu(self.linear(self.norm(x, prefix + ".norm2"), prefix + ".linear1")), prefix + ".linear2")
        x = self.norm(x[-1], "final_norm")
        return self.linear(self.gelu(self.linear(x, "regression_head.0")), "regression_head.3")

    def predict(self, values):
        original = np.asarray(values, dtype=np.float64)
        scaled = ((original - self.mean) / self.scale).astype(np.float32)
        result = self.forward_scaled(scaled).astype(np.float64) * self.scale + self.mean
        if not np.isfinite(result).all():
            raise ValueError("Model returned non-finite predictions.")
        prediction = dict(zip(KEYS, map(float, result)))
        if any(not telemetry.RANGES[key][0] <= value <= telemetry.RANGES[key][1] for key, value in prediction.items()):
            raise ValueError("Model prediction is outside the supported water sensor range.")
        return prediction


@lru_cache(maxsize=1)
def get_model():
    return PondTransformer()


def status():
    model = get_model()
    return {key: model.config[key] for key in ("model_name", "checkpoint_sha256", "sequence_length", "features_in_order", "prediction_horizon_minutes", "best_epoch", "runtime", "parity_max_abs_error")}


def prepare_window(history, latest, demo_oxygen=False):
    """20-minute sampling; demo values remain ephemeral and explicitly labeled."""
    if not demo_oxygen and (latest.get("quality") != "ok" or telemetry.is_stale(latest)):
        raise HTTPException(409, "Live Transformer needs recent, valid temperature, pH, dissolved oxygen and turbidity readings. Choose the oxygen demo to try generated values.")
    anchor = telemetry.parse_time(latest["ts"])
    records = []
    for row in history:
        required = ("water_temperature", "ph", "turbidity") if demo_oxygen else KEYS
        if (demo_oxygen or row.get("quality") == "ok") and all(isinstance(row.get(key), (int, float)) and math.isfinite(row[key]) and telemetry.RANGES[key][0] <= row[key] <= telemetry.RANGES[key][1] for key in required):
            records.append((telemetry.parse_time(row["ts"]), row))
    if not records:
        raise HTTPException(409, "The demo still needs temperature, pH and calibrated turbidity readings from your pond.")
    records.sort(key=lambda item: item[0])
    window = []
    used_stamps = set()
    method = "nearest_sensor_sample_within_10_minutes"
    for step in range(12):
        target = anchor - timedelta(minutes=20 * (11 - step))
        stamp, row = min(records, key=lambda item: abs((item[0] - target).total_seconds()))
        if abs((stamp - target).total_seconds()) > 600 or stamp in used_stamps:
            if not demo_oxygen:
                raise HTTPException(409, "Transformer needs 12 complete readings covering a 20-minute sampling window. Keep sensors sending data for about four hours.")
            method = "demo_history_seeded_from_available_snapshots"
        used_stamps.add(stamp)
        window.append({"ts": target.isoformat(), **{key: row.get(key) for key in KEYS}})
    if demo_oxygen:
        generator = random.Random()
        oxygen = generator.uniform(5.5, 7.5)
        for row in window:
            oxygen = min(8.0, max(4.5, oxygen + generator.uniform(-0.18, 0.18)))
            row["dissolved_oxygen"] = round(oxygen, 3)
    return window, method
