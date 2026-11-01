"""Read prepared content for a camera-catalog object without provider calls."""

import hashlib
import json
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.modules.knowledge.models import PreparedContent
from app.modules.live_camera.inference import CameraFailure


def evidence_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for child in value.values() for s in evidence_strings(child)]
    if isinstance(value, list):
        return [s for child in value for s in evidence_strings(child)]
    return []


async def read_experience(
    database: AsyncDatabase[dict[str, Any]],
    object_id: str,
    location_id: str,
    source_ids: set[str],
) -> PreparedContent:
    obj = await database["objects"].find_one(
        {"_id": ObjectId(object_id), "location_id": location_id},
        {"prepared_content": 1, "source_ids": 1},
    )
    if obj is None:
        raise CameraFailure("OBJECT_NOT_FOUND", 404)
    if not obj.get("prepared_content"):
        raise CameraFailure("CONTENT_NOT_PREPARED", 404)
    prepared = PreparedContent.model_validate(obj["prepared_content"])
    ids = set(prepared.source_ids)
    if not ids or not ids <= source_ids or not ids <= set(obj.get("source_ids", [])):
        raise CameraFailure("CONTENT_UNAVAILABLE", 503)
    evidence = {}
    for sid in sorted(ids):
        source = await database["sources"].find_one(
            {"_id": ObjectId(sid), "location_id": location_id},
            {"raw_record": 1},
        )
        if source is None or not source.get("raw_record"):
            raise CameraFailure("CONTENT_UNAVAILABLE", 503)
        evidence[sid] = source["raw_record"]
    serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    if hashlib.sha256(serialized.encode()).hexdigest() != prepared.evidence_hash:
        raise CameraFailure("CONTENT_OUTDATED", 409)
    sections = [
        prepared.content.introduction,
        *prepared.content.facts,
        prepared.content.story,
        prepared.content.history,
        prepared.content.interesting_fact,
    ]
    if any(
        citation.source_id not in ids
        or not any(
            citation.quote in text
            for text in evidence_strings(evidence.get(citation.source_id))
        )
        for section in sections
        if section is not None
        for citation in section.citations
    ):
        raise CameraFailure("CONTENT_UNAVAILABLE", 503)
    return prepared
