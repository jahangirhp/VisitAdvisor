"""Explicit, read-only Atlas export and local ONNX conversion for a phone trial."""

import argparse
import asyncio
import hashlib
import importlib
import json
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bson import ObjectId

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.visual.features import (
    DIMENSIONS,
    DinoEncoder,
    VisualFailure,
    unit_vector,
)
from app.modules.visual.repository import VisualRepository, evidence_hash


def public_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
        ):
            return value
    except ValueError:
        pass
    return None


def catalog_object(
    obj: dict[str, Any], sources: list[dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any]:
    """Export public canonical fields, excluding raw and generated content."""
    if (
        record["object_id"] != str(obj["_id"])
        or record["location_id"] != obj["location_id"]
        or not sources
        or {str(s["_id"]) for s in sources} != set(obj.get("source_ids", []))
        or any(s["location_id"] != obj["location_id"] for s in sources)
        or record["evidence_hash"] != evidence_hash(obj, sources)
        or not 1 <= len(record["views"]) <= 4
    ):
        raise VisualFailure("CAMERA_REFERENCE_NOT_CURRENT")
    views = []
    for view in record["views"]:
        regions = view["derived"]["regions"]
        if not 1 <= len(regions) <= 5:
            raise VisualFailure("CAMERA_REFERENCE_INVALID")
        reference = view["reference"]
        views.append(
            {
                "vectors": [unit_vector(r["embedding"]) for r in regions],
                "source_ids": reference.get("source_ids", []),
                "image_url": public_url(reference.get("image_url")),
                "viewpoint": reference.get("viewpoint"),
                "rights_status": reference.get("rights_status", "unknown"),
            }
        )
    return {
        "id": str(obj["_id"]),
        "location_id": obj["location_id"],
        "name": obj["name"],
        "type": obj["type"],
        "creator": obj.get("creator"),
        "year": obj.get("year"),
        "description": obj.get("description"),
        "sources": [
            {"id": str(s["_id"]), "title": s["title"], "url": public_url(s.get("url"))}
            for s in sources
        ],
        "reference_set_id": record["set_id"],
        "evidence_hash": record["evidence_hash"],
        "views": views,
    }


def export_model(encoder: DinoEncoder, target: Path) -> dict[str, Any]:
    torch = encoder.torch
    np = encoder.np
    ort = importlib.import_module("onnxruntime")
    onnx = importlib.import_module("onnx")

    class Embedding(torch.nn.Module):  # type: ignore[name-defined,misc]
        def __init__(self) -> None:
            super().__init__()
            self.network = encoder.network

        def forward(self, pixel_values: Any) -> Any:
            output = self.network(pixel_values=pixel_values).last_hidden_state[:, 0, :]
            return torch.nn.functional.normalize(output, dim=-1)

    network = Embedding().eval()
    # Fixed shape keeps the first mobile runtime bounded and export reproducible.
    torch.manual_seed(0)
    sample = torch.randn(1, 3, 224, 224)
    with torch.inference_mode():
        torch.onnx.export(
            network,
            (sample,),
            str(target),
            input_names=["pixel_values"],
            output_names=["embedding"],
            opset_version=17,
            dynamo=False,
        )
    onnx.checker.check_model(str(target))
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(
        str(target), options, providers=["CPUExecutionProvider"]
    )
    maximum_error = 0.0
    for tensor in (sample, torch.zeros_like(sample), torch.ones_like(sample)):
        with torch.inference_mode():
            expected = network(tensor).numpy()
        actual = session.run(None, {"pixel_values": tensor.numpy()})[0]
        maximum_error = max(maximum_error, float(np.max(np.abs(expected - actual))))
    if maximum_error > 0.0001:
        raise VisualFailure("CAMERA_MODEL_PARITY_FAILED")
    return {
        "file": "model.onnx",
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "bytes": target.stat().st_size,
        "source_model": encoder.model,
        "dimensions": DIMENSIONS,
        "input_size": 224,
        "reference_preprocessing": encoder.preprocessing,
        "camera_preprocessing": "canvas-center-shortedge256-crop224-rgb-imagenet-v1",
        "onnx_version": onnx.__version__,
        "onnxruntime_version": ort.__version__,
        "tensor_parity_max_absolute_error": maximum_error,
        "camera_accuracy_validated": False,
    }


async def export_catalog(
    encoder: DinoEncoder, location_id: str, object_ids: list[str]
) -> dict[str, Any]:
    db = MongoDB(Settings())
    try:
        location = await db.database["locations"].find_one(
            {"_id": ObjectId(location_id)}
        )
        if location is None or location.get("status") != "active":
            raise VisualFailure("CAMERA_LOCATION_UNAVAILABLE")
        repository = VisualRepository(db.database)
        objects = []
        for oid in object_ids:
            obj = await db.database["objects"].find_one(
                {"_id": ObjectId(oid), "location_id": location_id}
            )
            record = await repository.current(location_id, oid)
            if (
                obj is None
                or record is None
                or record["model"] != encoder.model
                or record["preprocessing"] != encoder.preprocessing
            ):
                raise VisualFailure("CAMERA_REFERENCE_UNAVAILABLE")
            sources = await repository.sources(obj)
            objects.append(catalog_object(obj, sources, record))
        # Reject concurrent reference/source changes before publishing the snapshot.
        for item in objects:
            obj = await db.database["objects"].find_one({"_id": ObjectId(item["id"])})
            record = await repository.current(location_id, item["id"])
            if (
                obj is None
                or obj["location_id"] != location_id
                or record is None
                or record["set_id"] != item["reference_set_id"]
                or evidence_hash(obj, await repository.sources(obj))
                != item["evidence_hash"]
            ):
                raise VisualFailure("CAMERA_REFERENCE_CHANGED")
        now = datetime.now(UTC)
        return {
            "version": "live-camera-v1",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "location": {"id": location_id, "name": location["name"]},
            "objects": objects,
            "automatic_identification": False,
            "origin": "atlas-reference-snapshot",
            "atlas_freshness_verified": True,
            "policy": {
                "candidate_floor": 0.55,
                "candidate_margin": 0.04,
                "stable_frames": 3,
                "global_weight": 0.75,
                "calibrated": False,
            },
        }
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location-id", required=True)
    parser.add_argument("--object-id", action="append", required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/live-camera"))
    args = parser.parse_args()
    ids = list(dict.fromkeys(args.object_id))
    if not 2 <= len(ids) <= 12 or not all(
        ObjectId.is_valid(value) for value in [args.location_id, *ids]
    ):
        parser.error(
            "Use a valid location ID and between 2 and 12 distinct object IDs."
        )
    try:
        encoder = DinoEncoder()
        args.output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=args.output, prefix="build-") as temporary:
            stage = Path(temporary)
            model = export_model(encoder, stage / "model.onnx")
            catalog = asyncio.run(export_catalog(encoder, args.location_id, ids))
            catalog["model"] = model
            (stage / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
            # Model is deterministic; publish the catalog last. Hash verification
            # makes a client fail closed if it observes a mixed export during refresh.
            shutil.move(stage / "model.onnx", args.output / "model.onnx")
            shutil.move(stage / "catalog.json", args.output / "catalog.json")
        print(json.dumps({"objects": len(ids), "model": model, "atlas_writes": 0}))
    except Exception:
        # Database/model exceptions can include credentials, URLs or local paths.
        parser.exit(
            1,
            "Camera export failed; verify dependencies, references and Atlas access.\n",
        )


if __name__ == "__main__":
    main()
