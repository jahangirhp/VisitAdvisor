from pymongo.errors import DuplicateKeyError

from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.modules.locations.models import Location, LocationCreate
from app.modules.locations.repository import LocationRepository


class LocationService:
    def __init__(self, repository: LocationRepository) -> None:
        self.repository = repository

    async def create(self, data: LocationCreate) -> Location:
        try:
            return await self.repository.create(data)
        except DuplicateKeyError:
            raise ApplicationError(
                "LOCATION_SLUG_ALREADY_EXISTS",
                "A location with this slug already exists.",
                409,
            ) from None

    async def get(self, location_id: str) -> Location:
        record = await self.repository.get(validate_id(location_id))
        if record is None:
            raise ApplicationError("LOCATION_NOT_FOUND", "Location was not found.", 404)
        return record

    async def list(self, limit: int, offset: int) -> list[Location]:
        return await self.repository.list(limit, offset)
