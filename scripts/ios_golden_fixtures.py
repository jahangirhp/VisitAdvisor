"""Generate SYNTHETIC golden fixtures for the Swift package tests.

These fixtures exercise package parsing, validation, vector search and exhibit
ranking against the Python server arithmetic. They contain random unit vectors
and random SIFT-shaped arrays, not museum data, and say nothing about real
recognition accuracy.

    python -m scripts.ios_golden_fixtures
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from app.modules.live_camera.pipeline import PIPELINE_VERSION
from app.modules.live_camera.venue_package import DIMENSIONS, build_package

TARGET = Path("ios/VisitAdvisorKit/Tests/VisitAdvisorKitTests/Fixtures")


def unit(rng: np.random.Generator, n: int) -> list[list[float]]:
    m = rng.standard_normal((n, DIMENSIONS)).astype(np.float32)
    return (m / np.linalg.norm(m, axis=1, keepdims=True)).tolist()


def synthetic_catalog() -> dict:
    rng = np.random.default_rng(20261126)
    objects = []
    kinds = ["painting", "sculpture", "print", "vase", "statue", "drawing"]
    for index, kind in enumerate(kinds):
        planar = kind in {"painting", "print", "drawing"}
        views = []
        for side in range(1 + index % 3):
            view = {
                "vectors": unit(rng, 1 + (index + side) % 5),
                "image_hash": f"synthetic-{index}-{side}",
                "viewpoint": ["front", "left", "back"][side],
                "rights_status": "synthetic",
                "image_url": None,
            }
            if planar:
                count = 12 + index
                view["local"] = {
                    "points": rng.uniform(0, 200, (count, 2)).round(3).tolist(),
                    "descriptors": rng.integers(0, 255, (count, 128))
                    .astype(float)
                    .tolist(),
                    "size": [224, 224],
                }
            views.append(view)
        objects.append(
            {
                "id": f"5ynthetic{index:015d}",
                "location_id": "synthetic-venue",
                "name": f"Synthetic exhibit {index}",
                "type": kind,
                "creator": None,
                "year": None,
                "description": "Synthetic fixture; not museum content.",
                "sources": [
                    {"id": "fixture", "title": "Generated fixture", "url": None}
                ],
                "views": views,
                "recognition_profile": "planar" if planar else "multiview",
                "reference_view_coverage": {
                    "ready": True,
                    "distinct_images": len(views),
                },
            }
        )
    return {
        "reference_version": "0" * 63 + "1",
        "reference_signature": "0" * 63 + "2",
        "location": {"id": "synthetic-venue", "name": "Synthetic fixture venue"},
        "objects": objects,
        "created_at": datetime(2026, 11, 26, tzinfo=UTC).isoformat(),
        "automatic_identification": False,
        "model": {
            "dimensions": 384,
            "input_size": 224,
            "sha256": "0" * 63 + "3",
            "source_model": "facebook/dinov2-small@"
            "ed25f3a31f01632728cabb09d1542f84ab7b0056",
        },
        "policy": {
            "candidate_floor": 0.55,
            "candidate_margin": 0.04,
            "stable_frames": 3,
            "minimum_stable_ms": 600,
            "recognition_pipeline": PIPELINE_VERSION,
            "object_regions": True,
            "inference_budget_ms": 1500,
        },
    }


def main() -> None:
    catalog = synthetic_catalog()
    package = TARGET / "synthetic-package"
    if package.exists():
        shutil.rmtree(package)
    build_package(catalog, package)
    rng = np.random.default_rng(7)
    queries = []
    for case in range(8):
        # Mix random queries with near-duplicates of stored views so the
        # expected ranking has clear winners as well as close calls.
        obj = catalog["objects"][case % len(catalog["objects"])]
        base = np.asarray(obj["views"][0]["vectors"][0], np.float32)
        noise = rng.standard_normal(DIMENSIONS).astype(np.float32)
        query = base * (case % 4) + noise * 0.5
        query /= np.linalg.norm(query)
        scores = {}
        for item in catalog["objects"]:
            best = -1.0
            for view in item["views"]:
                s = np.asarray(view["vectors"], np.float32) @ query
                best = max(best, 0.75 * float(s[0]) + 0.25 * float(s.max()))
            scores[item["id"]] = best
        ranking = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        queries.append(
            {
                "query": query.tolist(),
                "embedding_scores": scores,
                "top3": [{"id": k, "score": v} for k, v in ranking],
            }
        )
    (TARGET / "ranking-golden.json").write_text(
        json.dumps({"synthetic": True, "tolerance": 1e-5, "cases": queries}, indent=1)
        + "\n"
    )
    preprocessing_golden()
    print(f"Wrote synthetic fixtures to {TARGET}")


def preprocessing_golden() -> None:
    """Bit-exact Pillow/NumPy outputs of the server camera path for one image."""
    from PIL import Image

    from app.modules.live_camera.regions import crop

    rng = np.random.default_rng(42)
    # Smooth gradients plus noise: random noise alone hides resampling bugs.
    yy, xx = np.mgrid[0:240, 0:320]
    base = np.stack([xx * 0.7, yy * 0.9, (xx + yy) * 0.4], axis=-1)
    pixels = np.clip(base + rng.normal(0, 25, base.shape), 0, 255).astype(np.uint8)
    (TARGET / "camera-320x240.rgb").write_bytes(pixels.tobytes())
    image = Image.fromarray(pixels)
    side = min(image.size) * 224 / 256
    box = (
        (image.width - side) / 2,
        (image.height - side) / 2,
        (image.width + side) / 2,
        (image.height + side) / 2,
    )
    frame = image.resize((512, 512), Image.Resampling.BICUBIC, box=box)
    region_box = (40 / 224, 31 / 224, 190 / 224, 205 / 224)
    region = crop(frame, region_box)
    encoded = region.resize((224, 224), Image.Resampling.BICUBIC)
    values = np.asarray(encoded, dtype=np.float32) / 255
    means = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    stds = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = np.ascontiguousarray(((values - means) / stds).transpose(2, 0, 1))
    proposal = frame.convert("RGB").resize((224, 224), Image.Resampling.BILINEAR)

    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    (TARGET / "preprocessing-golden.json").write_text(
        json.dumps(
            {
                "synthetic": True,
                "input": {"file": "camera-320x240.rgb", "width": 320, "height": 240},
                "frame512_sha256": sha(np.asarray(frame).tobytes()),
                "region_box": list(region_box),
                "region512_sha256": sha(np.asarray(region).tobytes()),
                "proposal224_bilinear_sha256": sha(np.asarray(proposal).tobytes()),
                "model_input_float32le_sha256": sha(tensor.astype("<f4").tobytes()),
                "model_input_first_values": tensor.reshape(-1)[:8].tolist(),
            },
            indent=1,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
