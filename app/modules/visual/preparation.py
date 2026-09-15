"""Explicit foreground preparation; no downloads during visitor matching."""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
from bson import BSON
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import PyMongoError

from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.visual.features import ImageEncoder, VisualFailure, digest
from app.modules.visual.references import MAX_VIEWS, ReferenceClient, validate_url
from app.modules.visual.repository import (
    VisualRepository,
    evidence_hash,
    evidence_snapshot,
)


class ManualReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_id: str
    source_ids: list[str] = Field(min_length=1, max_length=10)
    image_url: str
    viewpoint: str = Field(min_length=1, max_length=300)
    rights_status: str = Field(pattern="^(recorded|unknown)$")
    evidence: dict[str, Any]


async def prepare(
    repository: VisualRepository,
    location_id: str,
    encoder: ImageEncoder,
    http: httpx.AsyncClient,
    limit: int,
    offset: int = 0,
    manual: list[ManualReference] | None = None,
) -> dict[str, Any]:
    if not 1 <= limit <= 500 or offset < 0:
        raise VisualFailure("INVALID_PREPARATION_LIMIT")
    objects = await repository.objects(location_id)
    selected = objects[offset : offset + limit]
    manual_by_object: dict[str, list[dict[str, Any]]] = {}
    if manual is not None:
        ids = {str(o["_id"]) for o in selected}
        for entry in manual:
            validate_id(entry.object_id)
            validate_url(entry.image_url)
            if entry.object_id not in ids:
                raise VisualFailure("MANIFEST_OBJECT_OUTSIDE_SELECTION")
            for value in entry.source_ids:
                validate_id(value)
            items = manual_by_object.setdefault(entry.object_id, [])
            items.append(entry.model_dump(exclude={"object_id"}))
            if len(items) > MAX_VIEWS or len(
                {item["image_url"] for item in items}
            ) != len(items):
                raise VisualFailure("INVALID_REFERENCE_VIEW_COUNT")
    report: dict[str, Any] = {
        "attempted": len(selected),
        "prepared": 0,
        "skipped": 0,
        "failed": 0,
        "uncovered": 0,
        "objects": [],
        "model": encoder.model,
        "preprocessing": encoder.preprocessing,
    }
    for obj in selected:
        object_id = str(obj["_id"])
        warnings: list[str] = []
        try:
            sources = await repository.sources(obj)
            source_ids = {str(s["_id"]) for s in sources}
            previous = await repository.current(location_id, object_id)
            client = ReferenceClient(http)
            if manual is None:
                refs, warnings = await client.resolve(sources)
            else:
                refs = manual_by_object.get(object_id, [])
                warnings = (
                    ["REFERENCE_RIGHTS_UNKNOWN"]
                    if any(r["rights_status"] == "unknown" for r in refs)
                    else []
                )
            if not refs:
                report["uncovered"] += 1
                report["objects"].append(
                    {
                        "object_id": object_id,
                        "status": "uncovered",
                        "warnings": warnings or ["REFERENCE_IMAGES_MISSING"],
                    }
                )
                continue
            views = []
            reused = 0
            for ref in refs:
                if not set(ref["source_ids"]).issubset(source_ids):
                    raise VisualFailure("REFERENCE_SOURCE_MISMATCH")
                image, image_hash = await client.image(ref["image_url"])
                old = next(
                    (
                        v
                        for v in (previous or {}).get("views", [])
                        if v["image_hash"] == image_hash
                        and v["metadata_hash"] == digest(ref)
                    ),
                    None,
                )
                same_model = (
                    previous is not None
                    and previous["model"] == encoder.model
                    and previous["preprocessing"] == encoder.preprocessing
                )
                if old is not None and same_model:
                    derived = old["derived"]
                    reused += 1
                else:
                    derived = await asyncio.to_thread(
                        encoder.describe, image, regions=True
                    )
                # Crop coordinates refer to the normalized parent image.
                views.append(
                    {
                        "reference": ref,
                        "metadata_hash": digest(ref),
                        "image_hash": image_hash,
                        "normalized_image_hash": hashlib.sha256(image).hexdigest(),
                        "derived": derived,
                    }
                )
            current_hash = evidence_hash(obj, sources)
            unchanged = (
                previous is not None
                and reused == len(views)
                and digest(views) == digest(previous["views"])
                and current_hash == previous["evidence_hash"]
            )
            now = datetime.now(UTC)
            if unchanged:
                assert previous is not None
                metadata = {
                    "fetched_at": now,
                    "canonical_evidence": evidence_snapshot(obj, sources),
                }
                if len(BSON.encode({**previous, **metadata})) > 12 * 1024 * 1024:
                    raise VisualFailure("REFERENCE_SET_TOO_LARGE")
                # A refresh still records when the remote images/metadata were verified.
                await repository.references.update_one(
                    {"_id": previous["_id"], "set_id": previous["set_id"]},
                    {"$set": metadata},
                )
                status = "skipped"
            else:
                record = {
                    "location_id": location_id,
                    "object_id": object_id,
                    "model": encoder.model,
                    "preprocessing": encoder.preprocessing,
                    "evidence_hash": current_hash,
                    "set_id": uuid4().hex,
                    "views": views,
                    "vector_count": sum(
                        len(view["derived"]["regions"]) for view in views
                    ),
                    "warnings": warnings,
                    "fetched_at": now,
                }
                if len(BSON.encode(record)) > 12 * 1024 * 1024:
                    raise VisualFailure("REFERENCE_SET_TOO_LARGE")
                await repository.publish(record, previous)
                status = "prepared"
            report[status] += 1
            report["objects"].append(
                {
                    "object_id": object_id,
                    "status": status,
                    "views": len(views),
                    "warnings": warnings,
                }
            )
        except (VisualFailure, ApplicationError, PyMongoError) as exc:
            code = (
                str(exc)
                if isinstance(exc, VisualFailure)
                else exc.code
                if isinstance(exc, ApplicationError)
                else "VISUAL_DATABASE_FAILED"
            )
            report["failed"] += 1
            report["objects"].append(
                {"object_id": object_id, "status": "failed", "code": code}
            )
    return report
