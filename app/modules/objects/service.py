from bson import ObjectId

from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.locations.service import LocationService
from app.modules.objects.models import Object, ObjectCreate
from app.modules.objects.repository import ObjectRepository
from app.modules.sources.repository import SourceRepository


class ObjectService:
    def __init__(
        self,
        repository: ObjectRepository,
        locations: LocationService,
        sources: SourceRepository,
    ) -> None:
        self.repository = repository
        self.locations = locations
        self.sources = sources

    async def create(self, location_id: str, data: ObjectCreate) -> Object:
        location = await self.locations.get(location_id)
        if any(not ObjectId.is_valid(value) for value in data.source_ids):
            raise ApplicationError(
                "INVALID_SOURCE_REFERENCE", "Source references must be valid IDs.", 422
            )
        source_ids = list(
            dict.fromkeys(str(ObjectId(value)) for value in data.source_ids)
        )
        if source_ids and await self.sources.count_for_location(
            source_ids, location.id
        ) != len(source_ids):
            raise ApplicationError(
                "INVALID_SOURCE_REFERENCE",
                "Every referenced source must exist in this location.",
                422,
            )
        return await self.repository.create(
            data.model_copy(update={"source_ids": source_ids}), location.id
        )

    async def get(self, object_id: str) -> Object:
        record = await self.repository.get(validate_id(object_id))
        if record is None:
            raise ApplicationError("OBJECT_NOT_FOUND", "Object was not found.", 404)
        return record

    async def list(self, location_id: str, limit: int, offset: int) -> list[Object]:
        location = await self.locations.get(location_id)
        return await self.repository.list(location.id, limit, offset)
