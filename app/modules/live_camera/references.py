"""Read-only Atlas references with a bounded, per-process freshness lease."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from bson import ObjectId
from pymongo import timeout as mongo_timeout
from pymongo.asynchronous.database import AsyncDatabase
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.modules.admin.reference_focus import FOCUS_VERSION
from app.modules.live_camera.export import catalog_object
from app.modules.live_camera.geometry import requires_geometry, validate_local
from app.modules.live_camera.inference import (
    MAX_CAMERA_OBJECTS,
    CameraFailure,
    CameraRecognizer,
)
from app.modules.live_camera.pipeline import PIPELINE_VERSION, profile, view_coverage
from app.modules.visual.features import digest
from app.modules.visual.repository import evidence_hash

POLICY = {
    "candidate_floor": 0.55,
    "candidate_margin": 0.04,
    "stable_frames": 3,
    "global_weight": 0.75,
    "calibrated": False,
    "geometry_min_score": 0.4,
    "geometry_scope": "planar-and-scene-v2",
}


class AtlasCameraReferences:
    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        settings: Settings,
        directory: Path,
        discover_objects: bool = False,
    ) -> None:
        self.db = database
        self.discover_objects = discover_objects
        self.directory = directory
        self.location_id = settings.live_camera_location_id
        self.object_ids = (
            [] if discover_objects else sorted(set(settings.live_camera_object_ids))
        )
        self.policy: dict[str, Any] = dict(POLICY)
        if settings.live_camera_geometry_rerank:
            self.policy["geometry_ranking"] = "spatial-verification-v1"
        self.adaptive = settings.live_camera_adaptive
        self.calibration_path = settings.live_camera_calibration
        if self.calibration_path and not self.adaptive:
            raise ValueError("Camera calibration requires the adaptive pipeline")
        if self.adaptive:
            if settings.live_camera_geometry_rerank:
                raise ValueError("Choose either adaptive or geometry-only ranking")
            self.policy.update(
                {
                    "recognition_pipeline": PIPELINE_VERSION,
                    "frame_size": 512,
                    "object_regions": settings.live_camera_object_regions,
                    "inference_budget_ms": settings.live_camera_inference_budget_ms,
                    "minimum_stable_ms": 600,
                }
            )
        if (
            not self.location_id
            or not ObjectId.is_valid(self.location_id)
            or (not discover_objects and not 2 <= len(self.object_ids) <= 12)
            or not all(ObjectId.is_valid(oid) for oid in self.object_ids)
        ):
            raise ValueError(
                "Camera selection must contain a location and 2–12 objects"
            )
        # Model metadata is a build artifact, not a database/catalog snapshot.
        self.model = json.loads((directory / "model-metadata.json").read_text())
        self.interval = settings.live_camera_refresh_seconds
        self.lock = asyncio.Lock()
        self.checked_until = 0.0
        self.retry_after = 0.0
        self.recognizer: CameraRecognizer | None = None
        self.session: Any = None

    async def snapshot(self) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Poll metadata/evidence only; descriptors move only on a version change."""
        if self.discover_objects:
            records = (
                await self.db["visual_references"]
                .find({"location_id": self.location_id}, {"object_id": 1})
                .to_list(length=MAX_CAMERA_OBJECTS + 1)
            )
            if not records:
                raise CameraFailure("NO_PREPARED_REFERENCES")
            if len(records) > MAX_CAMERA_OBJECTS:
                raise CameraFailure("CAMERA_OBJECT_LIMIT")
            self.object_ids = sorted(
                {
                    record["object_id"]
                    for record in records
                    if ObjectId.is_valid(record.get("object_id"))
                }
            )
        location = await self.db["locations"].find_one(
            {"_id": ObjectId(self.location_id), "status": "active"}, {"name": 1}
        )
        if location is None:
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        objects = (
            await self.db["objects"]
            .find(
                {
                    "_id": {"$in": [ObjectId(oid) for oid in self.object_ids]},
                    "location_id": self.location_id,
                },
                {
                    key: 1
                    for key in (
                        "location_id",
                        "name",
                        "type",
                        "creator",
                        "year",
                        "description",
                        "external_id",
                        "source_ids",
                    )
                },
            )
            .to_list(length=MAX_CAMERA_OBJECTS + 1)
        )
        records = (
            await self.db["visual_references"]
            .find(
                {
                    "location_id": self.location_id,
                    "object_id": {"$in": self.object_ids},
                },
                {
                    key: 1
                    for key in (
                        "object_id",
                        "location_id",
                        "set_id",
                        "model",
                        "preprocessing",
                        "evidence_hash",
                    )
                },
            )
            .to_list(length=MAX_CAMERA_OBJECTS + 1)
        )
        if not self.discover_objects and (
            len(objects) != len(self.object_ids) or len(records) != len(self.object_ids)
        ):
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        source_ids = {sid for obj in objects for sid in obj.get("source_ids", [])}
        if not source_ids or len(source_ids) > MAX_CAMERA_OBJECTS * 100:
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        sources = (
            await self.db["sources"]
            .find(
                {
                    "_id": {
                        "$in": [
                            ObjectId(sid)
                            for sid in source_ids
                            if ObjectId.is_valid(sid)
                        ]
                    },
                    "location_id": self.location_id,
                },
                {key: 1 for key in ("location_id", "title", "url", "raw_record")},
            )
            .to_list(length=MAX_CAMERA_OBJECTS * 100 + 1)
        )
        by_source = {str(s["_id"]): s for s in sources}
        by_object = {str(obj["_id"]): obj for obj in objects}
        by_record = {r["object_id"]: r for r in records}
        if not self.discover_objects and (
            set(by_source) != source_ids or set(by_record) != set(self.object_ids)
        ):
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        rows = []
        for oid in self.object_ids:
            obj, record = by_object.get(oid), by_record.get(oid)
            if obj is None or record is None:
                if self.discover_objects:
                    continue
                raise CameraFailure("REFERENCES_UNAVAILABLE")
            linked = set(obj.get("source_ids", []))
            evidence = [by_source[sid] for sid in sorted(linked) if sid in by_source]
            if (
                not evidence
                or len(evidence) != len(linked)
                or record["model"] != self.model["source_model"]
                or record["preprocessing"] != self.model["reference_preprocessing"]
                or record["evidence_hash"] != evidence_hash(obj, evidence)
                or not record.get("set_id")
            ):
                if self.discover_objects:
                    continue
                raise CameraFailure("REFERENCES_UNAVAILABLE")
            rows.append({"object": obj, "record": record, "sources": evidence})
        if not rows:
            raise CameraFailure("NO_PREPARED_REFERENCES")
        self.reference_signature = digest(
            {"location": location, "rows": rows, "model": self.model}
        )
        if self.calibration_path is not None:
            from app.modules.live_camera.policy import CameraCalibration

            calibration = CameraCalibration.model_validate_json(
                self.calibration_path.read_bytes()
            )
            calibration.check_binding(
                self.reference_signature,
                self.model["sha256"],
                self.policy["object_regions"],
                self.policy["inference_budget_ms"],
            )
            self.policy["profiles"] = {
                key: value.model_dump() for key, value in calibration.profiles.items()
            }
            self.policy["calibration_hash"] = calibration.artifact_hash
        version = digest(
            {
                "location": location,
                "rows": rows,
                "model": self.model,
                "policy": self.policy,
            }
        )
        return version, {"id": self.location_id, "name": location["name"]}, rows

    async def load(self) -> CameraRecognizer:
        version, location, rows = await self.snapshot()
        now = datetime.now(UTC)
        expires = now + timedelta(days=7)
        if self.recognizer and self.recognizer.reference_version == version:
            self.recognizer.expires = expires
            self.recognizer.catalog["expires_at"] = expires.isoformat()
            return self.recognizer
        # Descriptor reads are independent and immutable-set constrained. Bound
        # concurrency so startup does not pay one network round trip per object.
        reads = asyncio.Semaphore(3)

        async def fetch(row: dict[str, Any]) -> Any:
            projection = {
                "object_id": 1,
                "location_id": 1,
                "set_id": 1,
                "evidence_hash": 1,
                "views.reference.source_ids": 1,
                "views.reference.image_url": 1,
                "views.reference.viewpoint": 1,
                "views.reference.rights_status": 1,
                "views.derived.regions": 1,
                "views.derived.preparation": 1,
                "views.image_hash": 1,
            }
            if requires_geometry(row["object"].get("type", "other"), self.policy):
                projection["views.derived.local"] = 1
            async with reads:
                # Larger immutable descriptor transfers need more time than a
                # metadata poll. The complete cold/refresh deadline still applies.
                with mongo_timeout(10):
                    return await self.db["visual_references"].find_one(
                        {
                            "location_id": self.location_id,
                            "object_id": str(row["object"]["_id"]),
                            "set_id": row["record"]["set_id"],
                        },
                        projection,
                    )

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(fetch(row)) for row in rows]
        objects = []
        for row, task in zip(rows, tasks, strict=True):
            record = task.result()
            if record is None:
                raise CameraFailure("REFERENCES_CHANGED", 409)
            focused = []
            for view in record["views"]:
                preparation = view["derived"].get("preparation") or {}
                if preparation.get("version") == FOCUS_VERSION:
                    if preparation.get("source_sha256") != view.get("image_hash"):
                        raise CameraFailure("REFERENCES_UNAVAILABLE")
                    focused.append(view)
            if focused:
                # Do not let an unselected full-scene fallback reintroduce the
                # background when this object has genuine selected references.
                record = {**record, "views": focused}
            obj = catalog_object(row["object"], row["sources"], record)
            for view, original in zip(obj["views"], record["views"], strict=True):
                reference_sources = set(view["source_ids"])
                if not reference_sources or not reference_sources <= set(
                    row["object"]["source_ids"]
                ):
                    raise CameraFailure("REFERENCES_UNAVAILABLE")
                if requires_geometry(obj.get("type", "other"), self.policy):
                    local = original["derived"]["local"]
                    # Older OpenCV references may exceed nfeatures at tied
                    # responses. Apply today's paired 800-feature cap in memory.
                    if len(local["points"]) != len(local["descriptors"]):
                        raise CameraFailure("REFERENCES_UNAVAILABLE")
                    local = {
                        **local,
                        "points": local["points"][:800],
                        "descriptors": local["descriptors"][:800],
                    }
                    validate_local(local)
                    view["local"] = local
                view["image_hash"] = original.get("image_hash")
            if self.adaptive:
                obj["recognition_profile"] = profile(obj.get("type", "other"))
                obj["reference_view_coverage"] = view_coverage(obj)
            objects.append(obj)
        catalog = {
            "version": "live-camera-v1",
            "reference_version": version,
            "reference_signature": self.reference_signature,
            "location": location,
            "objects": objects,
            "model": self.model,
            "policy": self.policy,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "automatic_identification": False,
            "origin": "atlas-live-references",
            "atlas_freshness_verified": True,
        }
        candidate = await run_in_threadpool(
            CameraRecognizer, self.directory, catalog, self.session
        )
        await run_in_threadpool(candidate.warm)
        # Do not publish references assembled across a concurrent source/set update.
        if (await self.snapshot())[0] != version:
            raise CameraFailure("REFERENCES_CHANGED", 409)
        self.session = candidate.session
        return candidate

    async def invalidate(self) -> None:
        """Drop deleted object descriptors immediately, retaining model weights."""
        async with self.lock:
            self.recognizer = None
            self.checked_until = 0.0
            self.retry_after = 0.0

    async def get(self) -> CameraRecognizer:
        async with self.lock:
            if self.recognizer is not None and monotonic() < self.checked_until:
                return self.recognizer
            if monotonic() < self.retry_after:
                raise CameraFailure("REFERENCES_UNAVAILABLE")
            try:
                # Cold preparation includes large descriptor transfers and model
                # warm-up. Ordinary metadata refresh keeps the shorter bound.
                # Scanning remains disabled until preparation has completed.
                async with asyncio.timeout(60 if self.recognizer is None else 20):
                    candidate = await self.load()
                self.recognizer = candidate
                self.checked_until = monotonic() + self.interval
                return candidate
            except Exception as error:
                # Never fall back to cached data after a failed freshness check.
                self.recognizer = None
                self.checked_until = 0
                self.retry_after = monotonic() + 2
                if isinstance(error, CameraFailure) and error.code in {
                    "NO_PREPARED_REFERENCES",
                    "CAMERA_OBJECT_LIMIT",
                }:
                    raise
                raise CameraFailure("REFERENCES_UNAVAILABLE") from None
