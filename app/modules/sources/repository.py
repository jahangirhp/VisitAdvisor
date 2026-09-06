from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.modules.sources.models import Source, SourceCreate


class SourceRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.collection = database["sources"]

    async def create(self, data: SourceCreate, location_id: str) -> Source:
        now = datetime.now(UTC)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        document = data.model_dump(mode="json")
        document.update(created_at=now, updated_at=now)
        document["location_id"] = location_id
        result = await self.collection.insert_one(document)
        document.pop("_id", None)
        return Source.model_validate({"id": str(result.inserted_id), **document})

    async def get(self, record_id: str) -> Source | None:
        document = await self.collection.find_one({"_id": ObjectId(record_id)})
        if document is None:
            return None
        return Source.model_validate({"id": str(document.pop("_id")), **document})

    async def count_for_location(self, source_ids: list[str], location_id: str) -> int:
        return await self.collection.count_documents(
            {
                "_id": {"$in": [ObjectId(value) for value in source_ids]},
                "location_id": location_id,
            }
        )

    async def list(self, location_id: str, limit: int, offset: int) -> list[Source]:
        cursor = (
            self.collection.find({"location_id": location_id})
            .sort("_id", 1)
            .skip(offset)
            .limit(limit)
        )
        return [
            Source.model_validate({"id": str(document.pop("_id")), **document})
            async for document in cursor
        ]
