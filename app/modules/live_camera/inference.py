"""Bounded CPU recognition against a source-linked, disposable reference snapshot."""

import hashlib
import importlib
import io
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from PIL import Image, UnidentifiedImageError

from app.modules.live_camera.geometry import describe_local, validate_local
from app.modules.visual.features import geometric_score


class CameraFailure(Exception):
    def __init__(self, code: str, status: int = 503) -> None:
        self.code = code
        self.status = status


class CameraRecognizer:
    def __init__(self, directory: Path) -> None:
        import numpy as np

        raw = (directory / "catalog.json").read_bytes()
        self.reference_version = hashlib.sha256(raw).hexdigest()
        self.catalog: dict[str, Any] = json.loads(raw)
        self.expires = datetime.fromisoformat(self.catalog["expires_at"])
        self.check_fresh()
        model = self.catalog["model"]
        policy = self.catalog["policy"]
        if (
            self.catalog["version"] != "live-camera-v1"
            or self.catalog["automatic_identification"] is not False
            or model["dimensions"] != 384
            or model["input_size"] != 224
            or policy["global_weight"] != 0.75
            or policy["calibrated"] is not False
            or not 0.45 <= policy["candidate_floor"] <= 1
            or not 0.02 <= policy["candidate_margin"] <= 1
            or not 3 <= policy["stable_frames"] <= 10
            or not 2 <= len(self.catalog["objects"]) <= 12
        ):
            raise ValueError("Invalid camera references")
        self.views: list[tuple[str, list[Any]]] = []
        self.geometry_enabled = policy.get("geometry_min_score") == 0.4
        if "geometry_min_score" in policy and not self.geometry_enabled:
            raise ValueError("Invalid camera geometry policy")
        self.local_views: dict[str, list[dict[str, Any]]] = {}
        ids: set[str] = set()
        for obj in self.catalog["objects"]:
            if (
                obj["location_id"] != self.catalog["location"]["id"]
                or obj["id"] in ids
                or not obj["sources"]
                or not 1 <= len(obj["views"]) <= 4
            ):
                raise ValueError("Invalid camera object")
            ids.add(obj["id"])
            views = []
            self.local_views[obj["id"]] = []
            for view in obj["views"]:
                if self.geometry_enabled:
                    validate_local(view["local"])
                    self.local_views[obj["id"]].append(view["local"])
                vectors = np.asarray(view["vectors"], dtype=np.float32)
                if (
                    vectors.ndim != 2
                    or vectors.shape[1] != 384
                    or not 1 <= vectors.shape[0] <= 5
                    or not np.isfinite(vectors).all()
                ):
                    raise ValueError("Invalid camera vectors")
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if np.any(norms <= 0):
                    raise ValueError("Empty camera vectors")
                views.append(vectors / norms)
            self.views.append((obj["id"], views))
        path = directory / "model.onnx"
        with path.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != model["sha256"]:
                raise ValueError("Invalid camera model")
        ort = importlib.import_module("onnxruntime")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def check_fresh(self) -> None:
        if self.expires <= datetime.now(UTC):
            raise CameraFailure("REFERENCES_EXPIRED")

    def public_catalog(self) -> dict[str, Any]:
        self.check_fresh()
        return {
            "version": "camera-server-v1",
            "processing": "server",
            "reference_version": self.reference_version,
            "automatic_identification": False,
            **{
                key: self.catalog[key]
                for key in (
                    "location",
                    "created_at",
                    "expires_at",
                    "policy",
                    "atlas_freshness_verified",
                    "origin",
                )
                if key in self.catalog
            },
            "credits": self.catalog.get("credits", []),
            "objects": [
                {
                    key: obj[key]
                    for key in (
                        "id",
                        "location_id",
                        "name",
                        "creator",
                        "year",
                        "type",
                        "description",
                        "sources",
                    )
                    if key in obj
                }
                for obj in self.catalog["objects"]
            ],
        }

    def recognize(self, data: bytes) -> dict[str, Any]:
        import numpy as np

        self.check_fresh()
        started = perf_counter()
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.format != "JPEG" or source.size != (224, 224):
                    raise CameraFailure("INVALID_FRAME", 422)
                rgb = source.convert("RGB")
                pixels = np.asarray(rgb, dtype=np.float32) / 255
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
        ):
            raise CameraFailure("INVALID_FRAME", 422) from None
        means = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        stds = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        tensor = np.ascontiguousarray(
            ((pixels - means) / stds).transpose(2, 0, 1)[None]
        )
        vector = np.asarray(
            self.session.run(["embedding"], {"pixel_values": tensor})[0],
            dtype=np.float32,
        ).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (384,) or not np.isfinite(vector).all() or norm <= 0:
            raise CameraFailure("RECOGNITION_UNAVAILABLE")
        vector /= norm
        local = describe_local(rgb) if self.geometry_enabled else None
        candidates: list[dict[str, Any]] = []
        for object_id, views in self.views:
            scores = [view @ vector for view in views]
            score = max(0.75 * float(s[0]) + 0.25 * float(s.max()) for s in scores)
            if (
                local is not None
                and max(
                    geometric_score(local, reference)
                    for reference in self.local_views[object_id]
                )
                < 0.4
            ):
                score = 0.0
            if not math.isfinite(score):
                raise CameraFailure("RECOGNITION_UNAVAILABLE")
            candidates.append({"id": object_id, "score": min(1.0, score)})
        candidates.sort(key=lambda item: (-float(item["score"]), str(item["id"])))
        self.check_fresh()
        return {
            "version": "camera-server-v1",
            "location_id": self.catalog["location"]["id"],
            "reference_version": self.reference_version,
            "automatic_identification": False,
            "candidates": candidates[:3],
            "inferenceMs": round((perf_counter() - started) * 1000, 2),
        }
