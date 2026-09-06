from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.modules.objects.models import Object, ObjectCreate


class ObjectRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.collection = database["objects"]

    async def create(self, data: ObjectCreate, location_id: str) -> Object:
        now = datetime.now(UTC)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        document = data.model_dump(mode="json")
        document.update(created_at=now, updated_at=now)
        document["location_id"] = location_id
        result = await self.collection.insert_one(document)
        document.pop("_id", None)
        return Object.model_validate({"id": str(result.inserted_id), **document})

    async def get(self, record_id: str) -> Object | None:
        document = await self.collection.find_one({"_id": ObjectId(record_id)})
        if document is None:
            return None
        return Object.model_validate({"id": str(document.pop("_id")), **document})

    async def list(self, location_id: str, limit: int, offset: int) -> list[Object]:
        cursor = (
            self.collection.find({"location_id": location_id})
            .sort("_id", 1)
            .skip(offset)
            .limit(limit)
        )
        return [
            Object.model_validate({"id": str(document.pop("_id")), **document})
            async for document in cursor
        ]
