from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_database
from app.modules.locations.models import Location, LocationCreate
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService

router = APIRouter(tags=["locations"])


def get_service(
    database: Annotated[AsyncDatabase[dict[str, Any]], Depends(get_database)],
) -> LocationService:
    return LocationService(LocationRepository(database))


Service = Annotated[LocationService, Depends(get_service)]


@router.post("/locations", status_code=201, summary="Create a location")
async def create_location(data: LocationCreate, service: Service) -> Location:
    return await service.create(data)


@router.get("/locations", summary="List locations")
async def list_locations(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Location]:
    return await service.list(limit, offset)


@router.get("/locations/{location_id}", summary="Retrieve a location")
async def get_location(location_id: str, service: Service) -> Location:
    return await service.get(location_id)
