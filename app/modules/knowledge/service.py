import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.knowledge.models import PreparedContent, VisitorContent
from app.modules.knowledge.provider import (
    PROMPT_VERSION,
    ContentGenerator,
    GenerationFailure,
)


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    return []


def validate_content(
    content: VisitorContent, evidence: dict[str, dict[str, Any]]
) -> None:
    sections = [
        content.introduction,
        *content.facts,
        content.story,
        content.history,
        content.interesting_fact,
    ]
    if not content.introduction:
        raise GenerationFailure("INSUFFICIENT_EVIDENCE")
    for section in sections:
        if section is None:
            continue
        for citation in section.citations:
            raw = evidence.get(citation.source_id)
            if raw is None or not any(
                citation.quote in value for value in strings(raw)
            ):
                raise GenerationFailure("INVALID_EVIDENCE_CITATION")
    for section, maximum in [(content.story, 180), (content.history, 350)]:
        if section and len(section.text.split()) > maximum:
            raise GenerationFailure("CONTENT_TOO_LONG")


class KnowledgeService:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.objects = database["objects"]
        self.sources = database["sources"]

    async def prepare(
        self, object_id: str, generator: ContentGenerator, force: bool = False
    ) -> Literal["generated", "skipped"]:
        obj = await self.objects.find_one({"_id": ObjectId(validate_id(object_id))})
        if obj is None:
            raise ApplicationError("OBJECT_NOT_FOUND", "Object was not found.", 404)
        ids = obj.get("source_ids", [])
        if not ids or any(not ObjectId.is_valid(value) for value in ids):
            raise GenerationFailure("MISSING_SOURCE_EVIDENCE")
        evidence: dict[str, dict[str, Any]] = {}
        for source_id in sorted(set(ids)):
            source = await self.sources.find_one(
                {"_id": ObjectId(source_id), "location_id": obj["location_id"]}
            )
            if source is None:
                raise GenerationFailure("INVALID_SOURCE_REFERENCE")
            if source.get("raw_record"):
                evidence[source_id] = source["raw_record"]
        if not evidence:
            raise GenerationFailure("MISSING_SOURCE_EVIDENCE")
        # Only canonical raw source records are sent, never existing generated content.
        serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        if len(serialized) > 120_000:
            raise GenerationFailure("EVIDENCE_TOO_LARGE")
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        old = obj.get("prepared_content")
        if (
            old
            and not force
            and old["evidence_hash"] == fingerprint
            and old["prompt_version"] == PROMPT_VERSION
            and old["provider"] == generator.provider
            and old.get("requested_model", old["model"]) == generator.model
        ):
            return "skipped"
        result = await generator.generate(serialized)
        validate_content(result.content, evidence)
        # Recheck evidence before writing: a concurrent import must not silently
        # make this generation claim to use a newer source snapshot.
        for source_id, raw in evidence.items():
            current = await self.sources.find_one(
                {"_id": ObjectId(source_id), "location_id": obj["location_id"]}
            )
            if current is None or current.get("raw_record") != raw:
                raise GenerationFailure("EVIDENCE_CHANGED")
        prepared = PreparedContent(
            content=result.content,
            source_ids=list(evidence),
            evidence_hash=fingerprint,
            provider=generator.provider,
            model=result.model,
            requested_model=generator.model,
            prompt_version=PROMPT_VERSION,
            generated_at=datetime.now(UTC),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        document = prepared.model_dump(mode="json")
        document["generated_at"] = prepared.generated_at
        document["requested_model"] = generator.model
        # Atomic replacement only after validation. Canonical fields remain intact.
        updated = await self.objects.update_one(
            {"_id": obj["_id"], "source_ids": ids, "prepared_content": old},
            {"$set": {"prepared_content": document}},
        )
        if updated.matched_count != 1:
            raise GenerationFailure("OBJECT_CHANGED")
        return "generated"

    async def experience(self, object_id: str) -> PreparedContent:
        obj = await self.objects.find_one({"_id": ObjectId(validate_id(object_id))})
        if obj is None:
            raise ApplicationError("OBJECT_NOT_FOUND", "Object was not found.", 404)
        if not obj.get("prepared_content"):
            raise ApplicationError(
                "CONTENT_NOT_PREPARED", "Content has not been prepared.", 404
            )
        return PreparedContent.model_validate(obj["prepared_content"])
