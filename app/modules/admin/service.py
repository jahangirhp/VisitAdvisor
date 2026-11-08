"""Operator-reviewed reference photos, separate from visitor frames and content."""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from bson import ObjectId
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.admin.capture import prepare_foreground_mask, validate_capture_source
from app.modules.admin.reference_focus import reference_image
from app.modules.live_camera.pipeline import profile
from app.modules.recognition.images import prepare_image
from app.modules.sources.models import SourceCreate
from app.modules.sources.repository import SourceRepository
from app.modules.visual.features import ImageEncoder, VisualFailure, digest
from app.modules.visual.references import MAX_VIEWS
from app.modules.visual.repository import VisualRepository, evidence_hash

PHOTO_FIELDS = {
    key: 1
    for key in (
        "object_id",
        "location_id",
        "viewpoint",
        "photographer",
        "capture_group",
        "capture_metadata",
        "foreground_mask",
        "rights",
        "source_id",
        "sha256",
        "normalized_sha256",
        "created_at",
        "slot",
    )
}
REFERENCE_FIELDS = {
    key: 1
    for key in (
        "object_id",
        "evidence_hash",
        "model",
        "preprocessing",
        "set_id",
        "views.reference",
        "views.image_hash",
        "views.normalized_image_hash",
    )
}


class PhotoService:
    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        planar_target: int = 3,
        multiview_target: int = 4,
    ) -> None:
        self.targets = (planar_target, multiview_target)
        self.db = database
        self.photos = database["admin_reference_photos"]
        self.visual = VisualRepository(database)

    async def object(self, location_id: str, object_id: str) -> dict[str, Any]:
        location_id, object_id = validate_id(location_id), validate_id(object_id)
        obj = await self.db["objects"].find_one(
            {"_id": ObjectId(object_id), "location_id": location_id}
        )
        if obj is None:
            raise ApplicationError(
                "OBJECT_NOT_FOUND", "Object not found in this venue.", 404
            )
        return obj

    async def venue(self, location_id: str) -> None:
        if not await self.db["locations"].find_one(
            {"_id": ObjectId(validate_id(location_id))}, {"_id": 1}
        ):
            raise ApplicationError("LOCATION_NOT_FOUND", "Venue not found.", 404)

    async def delete_object(self, location_id: str, object_id: str) -> None:
        location_id, object_id = validate_id(location_id), validate_id(object_id)
        query = {"_id": ObjectId(object_id), "location_id": location_id}
        related = {"object_id": object_id}

        async def remove(session: AsyncClientSession) -> None:
            if (
                await self.db["objects"].find_one(query, {"_id": 1}, session=session)
                is None
            ):
                raise ApplicationError(
                    "OBJECT_NOT_FOUND", "Object not found in this venue.", 404
                )
            # Remove every model/version and inactive vector set for this object.
            # Canonical sources can be shared with other objects and stay intact.
            for collection in (
                "visual_vectors",
                "visual_references",
                "knowledge_chunks",
                "admin_reference_photos",
            ):
                await self.db[collection].delete_many(related, session=session)
            await self.db["objects"].delete_one(query, session=session)

        try:
            async with asyncio.timeout(20):
                async with self.db.client.start_session() as session:
                    await session.with_transaction(
                        remove,
                        read_concern=ReadConcern("snapshot"),
                        write_concern=WriteConcern("majority"),
                        max_commit_time_ms=5000,
                    )
        except (PyMongoError, TimeoutError):
            raise ApplicationError(
                "OBJECT_DELETE_UNAVAILABLE",
                "Could not finish deleting the object. Please retry.",
                503,
            ) from None

    def coverage(
        self,
        obj: dict[str, Any],
        record: dict[str, Any] | None,
        photos: list[dict[str, Any]],
    ) -> dict[str, Any]:
        views = (record or {}).get("views", [])
        active = {v["image_hash"] for v in views if v.get("image_hash")}
        normalized = {v.get("normalized_image_hash") for v in views}
        pending = [
            p
            for p in photos
            if p["sha256"] not in active and p["normalized_sha256"] not in normalized
        ]
        target = self.targets[0 if profile(obj["type"]) == "planar" else 1]
        return {
            "id": str(obj["_id"]),
            "name": obj["name"],
            "type": obj["type"],
            "target": target,
            "prepared": len(active),
            "pending": len(pending),
            "missing": max(0, target - len(active) - len(pending)),
            "needs_photos": len(active) + len(pending) < target,
        }

    async def inventory(
        self, location_id: str, offset: int, limit: int
    ) -> dict[str, Any]:
        await self.venue(location_id)
        objects = (
            await self.db["objects"]
            .find(
                {"location_id": location_id},
                {
                    "name": 1,
                    "type": 1,
                    "source_ids": 1,
                    "location_id": 1,
                    "external_id": 1,
                },
            )
            .sort("_id", 1)
            .skip(offset)
            .limit(limit)
            .to_list()
        )
        ids = [str(obj["_id"]) for obj in objects]
        records = await self.visual.references.find(
            {"location_id": location_id, "object_id": {"$in": ids}}, REFERENCE_FIELDS
        ).to_list()
        photos = await self.photos.find(
            {"location_id": location_id, "object_id": {"$in": ids}}, PHOTO_FIELDS
        ).to_list()
        source_ids = {sid for obj in objects for sid in obj.get("source_ids", [])}
        sources = (
            await self.db["sources"]
            .find(
                {
                    "location_id": location_id,
                    "_id": {"$in": [ObjectId(sid) for sid in source_ids]},
                }
            )
            .to_list()
        )
        by_source = {str(source["_id"]): source for source in sources}
        rows = []
        for obj in objects:
            record = next(
                (r for r in records if r["object_id"] == str(obj["_id"])), None
            )
            evidence = [
                by_source[sid] for sid in obj.get("source_ids", []) if sid in by_source
            ]
            if record and (
                not evidence
                or len(evidence) != len(obj.get("source_ids", []))
                or record["evidence_hash"] != evidence_hash(obj, evidence)
            ):
                record = None
            rows.append(
                self.coverage(
                    obj,
                    record,
                    [p for p in photos if p["object_id"] == str(obj["_id"])],
                )
            )
        return {"objects": rows, "offset": offset, "limit": limit}

    async def detail(self, location_id: str, object_id: str) -> dict[str, Any]:
        obj = await self.object(location_id, object_id)
        sources = await self.visual.sources(obj) if obj.get("source_ids") else []
        record = await self.visual.references.find_one(
            {"location_id": location_id, "object_id": object_id}, REFERENCE_FIELDS
        )
        stale = bool(record and record["evidence_hash"] != evidence_hash(obj, sources))
        photos = (
            await self.photos.find(
                {"location_id": location_id, "object_id": object_id}, PHOTO_FIELDS
            )
            .sort("created_at", 1)
            .to_list()
        )
        return {
            **self.coverage(obj, None if stale else record, photos),
            "stale": stale,
            "sources": [{"id": str(s["_id"]), "title": s["title"]} for s in sources],
            "photos": [
                {**p, "_id": str(p["_id"]), "created_at": p["created_at"].isoformat()}
                for p in photos
            ],
            "views": [
                {
                    "viewpoint": v.get("reference", {}).get("viewpoint")
                    or "Unlabelled",
                    "rights_status": v.get("reference", {}).get(
                        "rights_status", "unknown"
                    ),
                }
                for v in (record or {}).get("views", [])
            ],
        }

    async def upload(
        self,
        location_id: str,
        object_id: str,
        data: bytes,
        content_type: str | None,
        metadata: dict[str, Any],
        *,
        foreground_mask: bytes | None = None,
        mask_content_type: str | None = None,
    ) -> str:
        validate_capture_source(metadata, data, foreground_mask)
        obj = await self.object(location_id, object_id)
        sources = await self.visual.sources(obj) if obj.get("source_ids") else []
        normalized = await asyncio.to_thread(prepare_image, data, content_type)
        mask_fields: dict[str, Any] = {}
        if foreground_mask is not None:
            if not metadata.get("capture_metadata"):
                raise ApplicationError(
                    "INVALID_CAPTURE_METADATA",
                    "A foreground mask requires capture details.",
                    422,
                )
            mask_data, mask_metadata = await asyncio.to_thread(
                prepare_foreground_mask, foreground_mask, mask_content_type, normalized
            )
            mask_fields = {
                "foreground_mask": mask_metadata,
                "foreground_mask_data": mask_data,
            }
        if not metadata["source_id"]:
            if sources:
                metadata["source_id"] = str(sources[0]["_id"])
            else:
                # Record the test association explicitly, without claiming official
                # evidence or inventing photographer/permission information.
                source = await SourceRepository(self.db).create(
                    SourceCreate(
                        title=f"Admin test photo association: {obj['name']}",
                        type="manual",
                        publisher="Admin test upload",
                    ),
                    location_id,
                )
                await self.db["objects"].update_one(
                    {"_id": obj["_id"], "location_id": location_id},
                    {"$addToSet": {"source_ids": source.id}},
                )
                metadata["source_id"] = source.id
                obj = await self.object(location_id, object_id)
                sources = await self.visual.sources(obj)
        if metadata["source_id"] not in {str(s["_id"]) for s in sources}:
            raise ApplicationError(
                "SOURCE_MISMATCH", "Select this object's source.", 422
            )
        sha = hashlib.sha256(data).hexdigest()
        clean_sha = hashlib.sha256(normalized).hexdigest()
        details = await self.detail(location_id, object_id)
        current = await self.visual.current(location_id, object_id)
        if any(
            v.get("image_hash") == sha or v.get("normalized_image_hash") == clean_sha
            for v in (current or {}).get("views", [])
        ):
            raise ApplicationError(
                "DUPLICATE_PHOTO", "This photo is already a reference.", 409
            )
        if details["prepared"] + details["pending"] >= MAX_VIEWS:
            raise ApplicationError(
                "PHOTO_LIMIT", "The pilot supports four views per object.", 409
            )
        used = {p["slot"] for p in details["photos"]}
        slot = next((i for i in range(MAX_VIEWS) if i not in used), None)
        if slot is None:
            raise ApplicationError(
                "PHOTO_LIMIT", "Four uploads already exist for this object.", 409
            )
        try:
            result = await self.photos.insert_one(
                {
                    "location_id": location_id,
                    "object_id": object_id,
                    "slot": slot,
                    **metadata,
                    **mask_fields,
                    "sha256": sha,
                    "normalized_sha256": clean_sha,
                    "data": data,
                    "content_type": content_type,
                    "created_at": datetime.now(UTC),
                }
            )
        except DuplicateKeyError:
            raise ApplicationError(
                "PHOTO_CONFLICT", "Duplicate photo or concurrent upload; refresh.", 409
            ) from None
        return str(result.inserted_id)

    async def publish(
        self,
        location_id: str,
        object_id: str,
        encoder: ImageEncoder,
        *,
        allow_existing: bool = False,
    ) -> dict[str, Any]:
        obj = await self.object(location_id, object_id)
        sources = await self.visual.sources(obj) if obj.get("source_ids") else []
        previous = await self.visual.current(location_id, object_id)
        current_hash = evidence_hash(obj, sources)
        if previous and (
            previous["evidence_hash"] != current_hash
            or previous["model"] != encoder.model
            or previous["preprocessing"] != encoder.preprocessing
        ):
            raise ApplicationError(
                "REFERENCES_INCOMPATIBLE",
                "Prepare existing references with the current model and sources first.",
                409,
            )
        views = list((previous or {}).get("views", []))
        photos = (
            await self.photos.find({"location_id": location_id, "object_id": object_id})
            .sort("slot", 1)
            .limit(MAX_VIEWS)
            .to_list()
        )
        source_ids = {str(s["_id"]) for s in sources}
        changed = False
        for photo in photos:
            image, preparation = await asyncio.to_thread(
                reference_image, photo, obj.get("type", "other")
            )
            existing_index = next(
                (
                    index
                    for index, view in enumerate(views)
                    if view["image_hash"] == photo["sha256"]
                    or view.get("normalized_image_hash") == photo["normalized_sha256"]
                ),
                None,
            )
            if (
                existing_index is not None
                and views[existing_index]["derived"].get("preparation") == preparation
            ):
                continue
            if photo["source_id"] not in source_ids:
                raise ApplicationError(
                    "SOURCE_MISMATCH", "Photo source no longer belongs to object.", 409
                )
            if existing_index is None and len(views) >= MAX_VIEWS:
                raise ApplicationError(
                    "PHOTO_LIMIT", "Publishing would exceed four reference views.", 409
                )
            capture_metadata = photo.get("capture_metadata")
            reference: dict[str, Any] = {
                "source_ids": [photo["source_id"]],
                "image_url": f"admin-upload:{photo['_id']}",
                "viewpoint": (
                    None
                    if capture_metadata
                    and not capture_metadata.get("viewpoint_verified")
                    else photo["viewpoint"]
                ),
                "rights_status": "recorded",
                "evidence": {
                    "upload_id": str(photo["_id"]),
                    **{
                        key: photo[key]
                        for key in ("photographer", "capture_group", "rights", "sha256")
                    },
                },
            }
            if capture_metadata:
                reference["evidence"]["capture_metadata"] = capture_metadata
            if photo.get("foreground_mask"):
                reference["evidence"]["foreground_mask"] = photo["foreground_mask"]
            if preparation is not None:
                reference["evidence"]["reference_preparation"] = preparation
            derived = await asyncio.to_thread(encoder.describe, image, regions=True)
            if preparation is not None:
                derived["preparation"] = preparation
            view = {
                "reference": reference,
                "metadata_hash": digest(reference),
                "image_hash": photo["sha256"],
                "normalized_image_hash": photo["normalized_sha256"],
                "derived": derived,
            }
            if existing_index is None:
                views.append(view)
            else:
                views[existing_index] = view
            changed = True
        if allow_existing and previous and views and not changed:
            return {"set_id": previous["set_id"], "prepared": len(views)}
        if not views or not changed:
            raise ApplicationError(
                "NO_PENDING_PHOTOS", "No new photos to prepare.", 409
            )
        record = {
            "location_id": location_id,
            "object_id": object_id,
            "model": encoder.model,
            "preprocessing": encoder.preprocessing,
            "evidence_hash": current_hash,
            "set_id": uuid4().hex,
            "views": views,
            "vector_count": sum(len(v["derived"]["regions"]) for v in views),
            "warnings": list((previous or {}).get("warnings", [])),
            "fetched_at": datetime.now(UTC),
        }
        try:
            await self.visual.publish(record, previous)
        except VisualFailure as exc:
            raise ApplicationError(
                "PREPARATION_CONFLICT",
                "References changed or exceeded limits; refresh and retry.",
                409,
            ) from exc
        return {"set_id": record["set_id"], "prepared": len(views)}
