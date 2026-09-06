from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.locations.service import LocationService
from app.modules.sources.models import Source, SourceCreate
from app.modules.sources.repository import SourceRepository


class SourceService:
    def __init__(
        self, repository: SourceRepository, locations: LocationService
    ) -> None:
        self.repository = repository
        self.locations = locations

    async def create(self, location_id: str, data: SourceCreate) -> Source:
        location = await self.locations.get(location_id)
        return await self.repository.create(data, location.id)

    async def get(self, source_id: str) -> Source:
        record = await self.repository.get(validate_id(source_id))
        if record is None:
            raise ApplicationError("SOURCE_NOT_FOUND", "Source was not found.", 404)
        return record

    async def list(self, location_id: str, limit: int, offset: int) -> list[Source]:
        location = await self.locations.get(location_id)
        return await self.repository.list(location.id, limit, offset)
