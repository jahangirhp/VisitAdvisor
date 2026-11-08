"""Bounded CPU recognition against a source-linked, disposable reference snapshot."""

import hashlib
import importlib
import io
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from PIL import Image, ImageStat, UnidentifiedImageError

from app.modules.live_camera.geometry import (
    describe_local,
    requires_geometry,
    validate_local,
)
from app.modules.visual.features import (
    geometric_evidence,
    geometric_score,
    verified_similarity,
)

MAX_CAMERA_OBJECTS = 500


class CameraFailure(Exception):
    def __init__(self, code: str, status: int = 503) -> None:
        self.code = code
        self.status = status


class CameraRecognizer:
    def __init__(
        self,
        directory: Path,
        catalog: dict[str, Any] | None = None,
        session: Any = None,
    ) -> None:
        import numpy as np

        # File input is retained only for explicit historical replay callers.
        raw = (
            (directory / "catalog.json").read_bytes()
            if catalog is None
            else json.dumps(catalog, sort_keys=True).encode()
        )
        self.catalog: dict[str, Any] = json.loads(raw)
        self.reference_version = self.catalog.get(
            "reference_version", hashlib.sha256(raw).hexdigest()
        )
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
            or not 1 <= len(self.catalog["objects"]) <= MAX_CAMERA_OBJECTS
        ):
            raise ValueError("Invalid camera references")
        self.views: list[tuple[str, list[Any]]] = []
        self.geometry_enabled = policy.get("geometry_min_score") == 0.4
        if "geometry_min_score" in policy and not self.geometry_enabled:
            raise ValueError("Invalid camera geometry policy")
        self.local_views: dict[str, list[dict[str, Any]]] = {}
        self.object_types: dict[str, str] = {}
        ranking = policy.get("geometry_ranking")
        if ranking not in (None, "spatial-verification-v1") or (
            ranking is not None and not self.geometry_enabled
        ):
            raise ValueError("Invalid camera geometry ranking")
        self.geometry_rerank = ranking is not None
        pipeline = policy.get("recognition_pipeline")
        if pipeline not in (None, "object-regions-types-v5") or (
            pipeline and self.geometry_rerank
        ):
            raise ValueError("Invalid camera recognition pipeline")
        self.adaptive = pipeline is not None
        self.warmed = False
        self.warm_ms = 0.0
        self._warm_gate = Lock()
        self.reference_coverage: dict[str, dict[str, Any]] = {}
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
            self.object_types[obj["id"]] = obj.get("type", "other")
            views = []
            self.local_views[obj["id"]] = []
            for view in obj["views"]:
                if self.geometry_enabled and requires_geometry(
                    obj.get("type", "other"), policy
                ):
                    validate_local(view["local"])
                    self.local_views[obj["id"]].append(
                        {
                            **view["local"],
                            "points": np.asarray(
                                view["local"]["points"], dtype=np.float32
                            ),
                            "descriptors": np.asarray(
                                view["local"]["descriptors"], dtype=np.float32
                            ),
                        }
                    )
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
            if self.adaptive:
                from app.modules.live_camera.pipeline import view_coverage

                self.reference_coverage[obj["id"]] = view_coverage(obj)
        if session is not None:
            self.session = session
            return
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

    def warm(self) -> None:
        """Exercise the model before enabling scanning; never create a fake match."""
        import numpy as np

        with self._warm_gate:
            if self.warmed:
                return
            self.check_fresh()
            started = perf_counter()
            try:
                vector = np.asarray(
                    self.session.run(
                        ["embedding"],
                        {"pixel_values": np.zeros((1, 3, 224, 224), dtype=np.float32)},
                    )[0]
                ).reshape(-1)
                if vector.shape != (384,) or not np.isfinite(vector).all():
                    raise ValueError("Invalid model output")
                if np.linalg.norm(vector) <= 0:
                    raise ValueError("Empty model output")
            except Exception:
                raise CameraFailure("MODEL_WARMUP_FAILED") from None
            self.check_fresh()
            self.warm_ms = (perf_counter() - started) * 1000
            self.warmed = True

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
            "readiness": {"model_warmed": self.warmed, "references_current": True},
            **{
                key: self.catalog[key]
                for key in (
                    "location",
                    "created_at",
                    "expires_at",
                    "policy",
                    "reference_signature",
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
                        "recognition_profile",
                        "reference_view_coverage",
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
                supported_sizes = (
                    {(224, 224), (512, 512)} if self.adaptive else {(224, 224)}
                )
                if source.format != "JPEG" or source.size not in supported_sizes:
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
        if getattr(self, "adaptive", False):
            from app.modules.live_camera.pipeline import recognize

            return recognize(self, rgb, started)
        if (
            self.catalog.get("policy", {}).get("geometry_scope")
            == "planar-and-scene-v2"
            and max(ImageStat.Stat(rgb).stddev) < 2
        ):
            return {
                "version": "camera-server-v1",
                "location_id": self.catalog["location"]["id"],
                "reference_version": self.reference_version,
                "automatic_identification": False,
                "candidates": [],
                "decision": "unreadable",
                "suggestion_allowed": False,
                "inferenceMs": round((perf_counter() - started) * 1000, 2),
            }
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
        geometry_rerank = getattr(self, "geometry_rerank", False)
        baseline = 0.0 if self.geometry_enabled else -1.0
        ranked_views = []
        for object_id, views in self.views:
            scores = []
            for view in views:
                similarities = view @ vector
                scores.append(
                    0.75 * float(similarities[0]) + 0.25 * float(similarities.max())
                )
            ranked_views.append((max(baseline, *scores), object_id, scores))
        if not geometry_rerank:
            ranked_views.sort(key=lambda row: (-row[0], row[1]))
        policy = self.catalog.get("policy", {})
        # Below floor minus margin, even an unverified upper bound cannot
        # establish a match or make an otherwise accepted match ambiguous.
        # Omit those weak candidates rather than publish unverified scores.
        minimum_bound = (
            policy.get("candidate_floor", -1.0) - policy.get("candidate_margin", 0.0)
            if policy.get("geometry_scope") == "planar-and-scene-v2"
            and not geometry_rerank
            else -1.0
        )
        local = None
        candidates: list[dict[str, Any]] = []
        for upper_bound, object_id, view_scores in ranked_views:
            if upper_bound < minimum_bound:
                break
            # Geometry can only reject an embedding score. Once an object's
            # best possible score is below third place, no remaining object
            # can change the response. Keep equal bounds for deterministic ties.
            if (
                not geometry_rerank
                and len(candidates) == 3
                and upper_bound < candidates[-1]["score"]
            ):
                break
            verify_geometry = self.geometry_enabled and requires_geometry(
                getattr(self, "object_types", {}).get(object_id, "other"),
                self.catalog.get("policy", {}),
            )
            score = baseline
            indices = sorted(
                range(len(view_scores)), key=view_scores.__getitem__, reverse=True
            )
            for index in indices:
                if not geometry_rerank and view_scores[index] <= score:
                    break
                if verify_geometry:
                    if local is None:
                        described = describe_local(rgb)
                        local = {
                            **described,
                            "points": np.asarray(described["points"], dtype=np.float32),
                            "descriptors": np.asarray(
                                described["descriptors"], dtype=np.float32
                            ),
                        }
                    # Embedding and geometry must still describe the same view.
                    reference = self.local_views[object_id][index]
                    if geometry_rerank:
                        evidence = geometric_evidence(local, reference)
                        score = max(
                            score,
                            verified_similarity(evidence, self.object_types[object_id]),
                        )
                        continue
                    if geometric_score(local, reference) < 0.4:
                        continue
                score = max(score, view_scores[index])
            if not math.isfinite(score):
                raise CameraFailure("RECOGNITION_UNAVAILABLE")
            candidates.append({"id": object_id, "score": min(1.0, score)})
            if not geometry_rerank:
                candidates.sort(
                    key=lambda item: (-float(item["score"]), str(item["id"]))
                )
                candidates = candidates[:3]
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
