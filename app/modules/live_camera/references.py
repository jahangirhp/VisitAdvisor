"""Read-only Atlas references with a bounded, per-process freshness lease."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.modules.live_camera.export import catalog_object
from app.modules.live_camera.geometry import validate_local
from app.modules.live_camera.inference import CameraFailure, CameraRecognizer
from app.modules.visual.features import digest
from app.modules.visual.repository import evidence_hash

POLICY = {
    "candidate_floor": 0.55,
    "candidate_margin": 0.04,
    "stable_frames": 3,
    "global_weight": 0.75,
    "calibrated": False,
    "geometry_min_score": 0.4,
}


class AtlasCameraReferences:
    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        settings: Settings,
        directory: Path,
    ) -> None:
        self.db = database
        self.directory = directory
        self.location_id = settings.live_camera_location_id
        self.object_ids = sorted(set(settings.live_camera_object_ids))
        if (
            not self.location_id
            or not ObjectId.is_valid(self.location_id)
            or not 2 <= len(self.object_ids) <= 12
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
            .to_list(length=13)
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
            .to_list(length=13)
        )
        if len(objects) != len(self.object_ids) or len(records) != len(self.object_ids):
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        source_ids = {sid for obj in objects for sid in obj.get("source_ids", [])}
        if not source_ids or len(source_ids) > 120:
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        sources = (
            await self.db["sources"]
            .find(
                {
                    "_id": {"$in": [ObjectId(sid) for sid in source_ids]},
                    "location_id": self.location_id,
                },
                {key: 1 for key in ("location_id", "title", "url", "raw_record")},
            )
            .to_list(length=121)
        )
        by_source = {str(s["_id"]): s for s in sources}
        by_object = {str(obj["_id"]): obj for obj in objects}
        by_record = {r["object_id"]: r for r in records}
        if set(by_source) != source_ids or set(by_record) != set(self.object_ids):
            raise CameraFailure("REFERENCES_UNAVAILABLE")
        rows = []
        for oid in self.object_ids:
            obj, record = by_object[oid], by_record[oid]
            evidence = [by_source[sid] for sid in sorted(obj.get("source_ids", []))]
            if (
                not evidence
                or record["model"] != self.model["source_model"]
                or record["preprocessing"] != self.model["reference_preprocessing"]
                or record["evidence_hash"] != evidence_hash(obj, evidence)
                or not record.get("set_id")
            ):
                raise CameraFailure("REFERENCES_UNAVAILABLE")
            rows.append({"object": obj, "record": record, "sources": evidence})
        version = digest(
            {"location": location, "rows": rows, "model": self.model, "policy": POLICY}
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
        objects = []
        for row in rows:
            record = await self.db["visual_references"].find_one(
                {
                    "location_id": self.location_id,
                    "object_id": str(row["object"]["_id"]),
                    "set_id": row["record"]["set_id"],
                }
            )
            if record is None:
                raise CameraFailure("REFERENCES_CHANGED", 409)
            obj = catalog_object(row["object"], row["sources"], record)
            for view, original in zip(obj["views"], record["views"], strict=True):
                reference_sources = set(view["source_ids"])
                if not reference_sources or not reference_sources <= set(
                    row["object"]["source_ids"]
                ):
                    raise CameraFailure("REFERENCES_UNAVAILABLE")
                local = original["derived"]["local"]
                validate_local(local)
                view["local"] = local
            objects.append(obj)
        catalog = {
            "version": "live-camera-v1",
            "reference_version": version,
            "location": location,
            "objects": objects,
            "model": self.model,
            "policy": POLICY,
            "created_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "automatic_identification": False,
            "origin": "atlas-live-references",
            "atlas_freshness_verified": True,
        }
        candidate = await run_in_threadpool(
            CameraRecognizer, self.directory, catalog, self.session
        )
        # Do not publish references assembled across a concurrent source/set update.
        if (await self.snapshot())[0] != version:
            raise CameraFailure("REFERENCES_CHANGED", 409)
        self.session = candidate.session
        return candidate

    async def get(self) -> CameraRecognizer:
        async with self.lock:
            if self.recognizer is not None and monotonic() < self.checked_until:
                return self.recognizer
            if monotonic() < self.retry_after:
                raise CameraFailure("REFERENCES_UNAVAILABLE")
            try:
                # Covers the complete refresh, not just each individual DB operation.
                async with asyncio.timeout(20):
                    candidate = await self.load()
                self.recognizer = candidate
                self.checked_until = monotonic() + self.interval
                return candidate
            except Exception:
                # Never fall back to cached data after a failed freshness check.
                self.recognizer = None
                self.checked_until = 0
                self.retry_after = monotonic() + 2
                raise CameraFailure("REFERENCES_UNAVAILABLE") from None
