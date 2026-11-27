"""Convert the pinned DINOv2-small camera encoder to Core ML and check parity.

Run on a Mac (Core ML prediction is only available on macOS):

    pip install -e ".[visual]" "coremltools>=8,<9"
    python -m scripts.convert_dinov2_coreml --download --output .local/coreml

The converted graph is the same wrapper the server exports to ONNX
(``live_camera/export.py``): ``pixel_values[1,3,224,224]`` (already ImageNet
normalized, RGB, NCHW float32) -> ``embedding[1,384]`` = L2-normalized CLS
token. Preprocessing stays in Swift so both platforms share one explicit
contract. The baseline is FLOAT32; FP16 must be evaluated separately against
the decision margins before it can replace this artifact.
"""

import argparse
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.modules.visual.features import DIMENSIONS, DinoEncoder

PARITY_LIMIT = 1e-4  # Max-abs error on the normalized embedding (same as ONNX).


def tree_sha256(path: Path) -> str:
    """Deterministic hash of an .mlpackage directory (relative paths + bytes)."""
    hasher = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        hasher.update(str(file.relative_to(path)).encode() + b"\0")
        hasher.update(file.read_bytes())
    return hasher.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/coreml"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    import coremltools as ct
    import numpy as np

    encoder = DinoEncoder(download=args.download)
    torch = encoder.torch

    class Embedding(torch.nn.Module):  # type: ignore[name-defined,misc]
        def __init__(self) -> None:
            super().__init__()
            self.network = encoder.network

        def forward(self, pixel_values: Any) -> Any:
            output = self.network(pixel_values=pixel_values).last_hidden_state[:, 0, :]
            return torch.nn.functional.normalize(output, dim=-1)

    network = Embedding().eval()
    torch.manual_seed(0)
    sample = torch.randn(1, 3, 224, 224)
    with torch.inference_mode():
        expected_before = network(sample).clone()

    # DINOv2 resizes its 37x37 position grid with bicubic interpolation on every
    # forward pass; Core ML has no bicubic upsample op. The input size is fixed
    # (224 -> 16x16 patches), so compute that tensor once with PyTorch and use
    # it as a constant. Values are identical, which is checked right below.
    embeddings = encoder.network.embeddings
    patches = (224 // embeddings.patch_size) ** 2
    with torch.inference_mode():
        fixed = embeddings.interpolate_pos_encoding(
            torch.zeros(1, patches + 1, embeddings.cls_token.shape[-1]), 224, 224
        ).clone()

    def fixed_position(embedded: Any, height: int, width: int) -> Any:
        return fixed

    embeddings.interpolate_pos_encoding = fixed_position
    with torch.inference_mode():
        if not torch.equal(network(sample), expected_before):
            raise SystemExit("Fixed position embedding changed the model output.")
    with torch.inference_mode():
        traced = torch.jit.trace(network, sample)
    model = ct.convert(
        traced,
        inputs=[
            ct.TensorType(name="pixel_values", shape=(1, 3, 224, 224), dtype=np.float32)
        ],
        outputs=[ct.TensorType(name="embedding", dtype=np.float32)],
        convert_to="mlprogram",
        compute_precision=ct.precision.FLOAT32,
        minimum_deployment_target=ct.target.iOS17,
    )
    model.short_description = (
        "DINOv2-small CLS embedding (L2-normalized), VisitAdvisor camera contract"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "DINOv2SmallCLS.mlpackage"
    model.save(str(target))

    results: dict[str, Any] = {
        "prediction": "not run (Core ML prediction requires macOS)"
    }
    if platform.system() == "Darwin":
        reloaded = ct.models.MLModel(str(target), compute_units=ct.ComputeUnit.CPU_ONLY)
        generator = torch.Generator().manual_seed(1)
        tensors = [sample, torch.zeros_like(sample), torch.ones_like(sample)] + [
            torch.randn(1, 3, 224, 224, generator=generator) for _ in range(5)
        ]
        maximum = 0.0
        minimum_cosine = 1.0
        for tensor in tensors:
            with torch.inference_mode():
                expected = network(tensor).numpy().reshape(-1)
            actual = np.asarray(
                reloaded.predict({"pixel_values": tensor.numpy()})["embedding"]
            ).reshape(-1)
            if actual.shape != (DIMENSIONS,) or not np.isfinite(actual).all():
                raise SystemExit("Core ML output has the wrong shape or is not finite.")
            maximum = max(maximum, float(np.max(np.abs(expected - actual))))
            minimum_cosine = min(minimum_cosine, float(expected @ actual))
        results = {
            "compute_units": "CPU_ONLY",
            "tensors": len(tensors),
            "max_abs_error": maximum,
            "min_cosine": minimum_cosine,
            "limit": PARITY_LIMIT,
            "passed": maximum <= PARITY_LIMIT,
        }
        if maximum > PARITY_LIMIT:
            (args.output / "coreml-metadata.json").write_text(
                json.dumps(results, indent=2)
            )
            raise SystemExit(f"Core ML parity failed: max abs error {maximum:.2e}.")

    metadata = {
        "file": target.name,
        "mlpackage_sha256": tree_sha256(target),
        "source_model": encoder.model,
        "dimensions": DIMENSIONS,
        "input": {
            "name": "pixel_values",
            "shape": [1, 3, 224, 224],
            "dtype": "float32",
        },
        "output": {
            "name": "embedding",
            "shape": [1, DIMENSIONS],
            "normalization": "l2",
        },
        "preprocessing_contract": "camera-preprocessing-v1",
        "compute_precision": "float32",
        "minimum_deployment_target": "iOS17",
        "versions": {
            name: version(name) for name in ("coremltools", "torch", "transformers")
        },
        "parity": results,
        "camera_accuracy_validated": False,
    }
    (args.output / "coreml-metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n"
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
