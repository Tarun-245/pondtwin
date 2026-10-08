"""Export the notebook checkpoint without quantization; PyTorch is export-only.

Run: python scripts/export_transformer.py /path/to/transformer_v1_best.pt
Requires torch==2.11.0 and numpy==2.2.6 in the export environment.
"""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class NotebookTransformer(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.input_projection = nn.Linear(4, 64)
        self.positional_embedding = nn.Parameter(torch.zeros(1, 12, 64))
        layer = nn.TransformerEncoderLayer(d_model=64, nhead=4, dim_feedforward=128,
                                          dropout=cfg["dropout"], activation="gelu",
                                          batch_first=True, norm_first=True)
        self.transformer_encoder = nn.TransformerEncoder(layer, num_layers=2)
        self.final_norm = nn.LayerNorm(64)
        self.regression_head = nn.Sequential(nn.Linear(64, 64), nn.GELU(),
                                            nn.Dropout(cfg["dropout"]), nn.Linear(64, 4))

    def forward(self, x):
        x = self.input_projection(x) + self.positional_embedding
        x = self.transformer_encoder(x)
        return self.regression_head(self.final_norm(x[:, -1, :]))


def main(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    cfg = checkpoint["model_config"]
    assert (cfg["sequence_length"], cfg["n_features"], cfg["d_model"], cfg["nhead"], cfg["num_encoder_layers"], cfg["dim_feedforward"]) == (12, 4, 64, 4, 2, 128)
    assert checkpoint["features_in_order"] == ["temperature", "ph", "dissolved_oxygen", "turbidity"]
    reference = NotebookTransformer(cfg).eval()
    reference.load_state_dict(checkpoint["model_state_dict"], strict=True)
    tensors = {key: value.detach().cpu().numpy() for key, value in checkpoint["model_state_dict"].items()}
    assert sum(value.size for value in tensors.values()) == 72580
    metadata = {
        "model_name": "TransformerV1", "checkpoint_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "model_config": cfg, "sequence_length": 12, "features_in_order": checkpoint["features_in_order"],
        "prediction_horizon_minutes": 20, "nominal_interval_minutes": 20,
        "best_epoch": checkpoint["best_epoch"], "runtime": "numpy_float32", "parity_max_abs_error": None,
        "scaler_mean": [27.576357957001328, 6.420086869655486, 9.731511414158527, 31.81050695264071],
        "scaler_scale": [5.060813065737878, 0.9732792979545929, 4.867075386623464, 10.748263445441804],
        "scaler_source": "data1/processed/splits/scaler_parameters.json; fitted on training only",
    }
    model_dir = ROOT / "models"
    model_dir.mkdir(exist_ok=True)
    np.savez_compressed(model_dir / "transformer_v1.npz", **tensors)
    config_path = model_dir / "transformer_v1.json"
    config_path.write_text(json.dumps(metadata, indent=2) + "\n")
    from app.transformer import PondTransformer
    exported = PondTransformer()
    generator = np.random.default_rng(42)
    cases = np.concatenate([np.zeros((1, 12, 4), dtype=np.float32),
                            generator.normal(0, 1, (32, 12, 4)).astype(np.float32),
                            generator.normal(0, 3, (16, 12, 4)).astype(np.float32)])
    torch.set_num_threads(1)
    with torch.inference_mode():
        expected = reference(torch.from_numpy(cases)).numpy()
    actual = np.stack([exported.forward_scaled(case) for case in cases])
    error = float(np.abs(expected - actual).max())
    np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=2e-5)
    metadata["parity_max_abs_error"] = error
    metadata["parity_test_windows"] = len(cases)
    config_path.write_text(json.dumps(metadata, indent=2) + "\n")
    # A fixture from the original framework verifies inference without requiring
    # PyTorch on the deployed service or in routine test runs.
    fixture = {"scaled_input": cases[1].tolist(), "scaled_prediction": expected[1].tolist()}
    (ROOT / "tests" / "transformer_reference.json").write_text(json.dumps(fixture) + "\n")
    print(json.dumps({"parameters": 72580, "windows_checked": len(cases), "maximum_scaled_error": error,
                      "exported_bytes": (model_dir / "transformer_v1.npz").stat().st_size}))


if __name__ == "__main__":
    main(sys.argv[1])
