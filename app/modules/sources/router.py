from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_database
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.sources.models import Source, SourceCreate
from app.modules.sources.repository import SourceRepository
from app.modules.sources.service import SourceService

router = APIRouter(tags=["sources"])


def get_service(
    database: Annotated[AsyncDatabase[dict[str, Any]], Depends(get_database)],
) -> SourceService:
    return SourceService(
        SourceRepository(database), LocationService(LocationRepository(database))
    )


Service = Annotated[SourceService, Depends(get_service)]


@router.post(
    "/locations/{location_id}/sources", status_code=201, summary="Create a source"
)
async def create_source(
    location_id: str, data: SourceCreate, service: Service
) -> Source:
    return await service.create(location_id, data)


@router.get("/locations/{location_id}/sources", summary="List sources")
async def list_sources(
    location_id: str,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Source]:
    return await service.list(location_id, limit, offset)


@router.get("/sources/{source_id}", summary="Retrieve a source")
async def get_source(source_id: str, service: Service) -> Source:
    return await service.get(source_id)
