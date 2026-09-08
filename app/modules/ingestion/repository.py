"""Explicit import upserts using the existing objects and sources collections."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.modules.ingestion.rijksmuseum import PROVIDER, ImportRecord


class ImportRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.objects = database["objects"]
        self.sources = database["sources"]

    async def save(
        self, location_id: str, record: ImportRecord
    ) -> Literal["imported", "updated", "skipped"]:
        now = datetime.now(UTC)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        key = {
            "location_id": location_id,
            "import_provider": PROVIDER,
            "external_id": record.external_id,
        }
        source = await self.sources.find_one(key)
        source_id = source["_id"] if source else ObjectId()
        source_update = {
            "$set": {
                "title": record.object.name,
                "url": record.external_id,
                "publisher": "Rijksmuseum",
                "type": "official_api",
                "raw_record": record.raw_record,
                "last_fetched_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {"_id": source_id, "created_at": now},
        }
        try:
            await self.sources.update_one(key, source_update, upsert=True)
        except DuplicateKeyError:
            # A concurrent importer may have created this evidence record first.
            await self.sources.update_one(key, source_update, upsert=False)
        source = await self.sources.find_one(key)
        assert source is not None
        source_id = source["_id"]
        incoming = record.object.model_dump(mode="json")
        fields = {
            name: value
            for name, value in incoming.items()
            if name in {"name", "type", "creator", "year", "description"}
            and value is not None
            and value != ""
        }
        if not record.type_provided:
            fields.pop("type", None)
        # Metadata and aliases are merged; visitor content is never overwritten.
        for name, value in incoming["metadata"]["rijksmuseum"].items():
            fields[f"metadata.rijksmuseum.{name}"] = value
        existing = await self.objects.find_one(key)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"fields": fields, "aliases": incoming["aliases"]}, sort_keys=True
            ).encode()
        ).hexdigest()
        if (
            existing
            and existing.get("import_hash") == fingerprint
            and str(source_id) in existing.get("source_ids", [])
        ):
            return "skipped"
        fields.update(updated_at=now, import_hash=fingerprint)
        update: dict[str, Any] = {
            "$set": fields,
            "$setOnInsert": {
                "created_at": now,
                "facts": [],
                "story": None,
                "history": None,
            },
            "$addToSet": {
                "source_ids": str(source_id),
                "aliases": {"$each": incoming["aliases"]},
            },
        }
        if not record.type_provided:
            update["$setOnInsert"]["type"] = "artwork"
        try:
            result = await self.objects.update_one(key, update, upsert=True)
        except DuplicateKeyError:
            result = await self.objects.update_one(key, update, upsert=False)
        return "imported" if result.upserted_id is not None else "updated"
