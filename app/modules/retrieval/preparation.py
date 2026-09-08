"""Explicit, bounded, repeatable embedding preparation, separate from sources."""

from datetime import UTC, datetime
from typing import Any

from app.modules.retrieval.provider import Embedder, RetrievalFailure, validate_vector
from app.modules.retrieval.repository import RetrievalRepository
from app.modules.retrieval.text import TEXT_VERSION, fingerprint, search_text


async def prepare(
    repository: RetrievalRepository,
    location_id: str,
    embedder: Embedder,
    limit: int,
    offset: int = 0,
) -> dict[str, Any]:
    objects = (await repository.objects(location_id))[offset : offset + limit]
    sources = await repository.source_ids(location_id)
    report: dict[str, Any] = {
        "attempted": len(objects),
        "generated": 0,
        "skipped": 0,
        "failed": 0,
        "input_tokens": 0,
        "errors": [],
    }
    pending = []
    for obj in objects:
        key: dict[str, Any] = {
            "location_id": location_id,
            "object_id": str(obj["_id"]),
            "content_type": "identity",
            "embedding_model": embedder.model,
            "embedding_dimensions": embedder.dimensions,
        }
        digest = fingerprint(obj)
        text = search_text(obj)
        source_ids = obj.get("source_ids", [])
        if not source_ids or not set(source_ids) <= sources:
            code = "MISSING_SOURCE_EVIDENCE"
        elif len(text.encode("utf-8")) > 6000:
            code = "EMBEDDING_INPUT_TOO_LARGE_OR_EMPTY"
        else:
            code = None
        if code:
            report["failed"] += 1
            report["errors"].append({"object_id": key["object_id"], "code": code})
            continue
        previous = await repository.chunks.find_one(key)
        if previous and previous.get("content_hash") == digest:
            try:
                validate_vector(previous.get("embedding"), embedder.dimensions)
                report["skipped"] += 1
                continue
            except RetrievalFailure:
                pass
        pending.append((obj, key, digest, text))
    for start in range(0, len(pending), 32):
        batch = pending[start : start + 32]
        try:
            result = await embedder.embed([row[3] for row in batch])
            if len(result.vectors) != len(batch):
                raise RetrievalFailure("INVALID_EMBEDDING")
            vectors = [validate_vector(v, embedder.dimensions) for v in result.vectors]
        except RetrievalFailure as exc:
            # Abort remaining paid work on a provider failure; preserve saved chunks.
            for _, key, _, _ in pending[start:]:
                report["failed"] += 1
                report["errors"].append(
                    {"object_id": key["object_id"], "code": str(exc)}
                )
            report["aborted"] = True
            break
        report["input_tokens"] += result.input_tokens
        current_sources = await repository.source_ids(location_id)
        for (obj, key, digest, text), vector in zip(batch, vectors, strict=True):
            current = await repository.get_object(location_id, key["object_id"])
            if (
                current is None
                or fingerprint(current) != digest
                or not set(obj["source_ids"]) <= current_sources
            ):
                report["failed"] += 1
                report["errors"].append(
                    {"object_id": key["object_id"], "code": "EVIDENCE_CHANGED"}
                )
                continue
            now = datetime.now(UTC)
            await repository.chunks.update_one(
                key,
                {
                    "$set": {
                        "text": text,
                        "content_hash": digest,
                        "embedding": vector,
                        "text_version": TEXT_VERSION,
                        "source_ids": obj["source_ids"],
                        "origin": "canonical_object",
                        "embedding_provider": "openai",
                        "language": "und",
                        "updated_at": now,
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            report["generated"] += 1
    return report
