from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.modules.locations.models import Location, LocationData


class LocationRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self.collection = database["locations"]

    async def create(self, data: LocationData) -> Location:
        now = datetime.now(UTC)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        document = data.model_dump(mode="json")
        document.update(created_at=now, updated_at=now)
        result = await self.collection.insert_one(document)
        document.pop("_id", None)
        return Location.model_validate({"id": str(result.inserted_id), **document})

    async def get(self, record_id: str) -> Location | None:
        document = await self.collection.find_one({"_id": ObjectId(record_id)})
        if document is None:
            return None
        return Location.model_validate({"id": str(document.pop("_id")), **document})

    async def list(self, limit: int, offset: int) -> list[Location]:
        cursor = self.collection.find({}).sort("_id", 1).skip(offset).limit(limit)
        return [
            Location.model_validate({"id": str(document.pop("_id")), **document})
            async for document in cursor
        ]
