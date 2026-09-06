from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.db.mongodb import get_database
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.objects.models import Object, ObjectCreate
from app.modules.objects.repository import ObjectRepository
from app.modules.objects.service import ObjectService
from app.modules.sources.repository import SourceRepository

router = APIRouter(tags=["objects"])


def get_service(
    database: Annotated[AsyncDatabase[dict[str, Any]], Depends(get_database)],
) -> ObjectService:
    return ObjectService(
        ObjectRepository(database),
        LocationService(LocationRepository(database)),
        SourceRepository(database),
    )


Service = Annotated[ObjectService, Depends(get_service)]


@router.post(
    "/locations/{location_id}/objects", status_code=201, summary="Create an object"
)
async def create_object(
    location_id: str, data: ObjectCreate, service: Service
) -> Object:
    return await service.create(location_id, data)


@router.get("/locations/{location_id}/objects", summary="List objects")
async def list_objects(
    location_id: str,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Object]:
    return await service.list(location_id, limit, offset)


@router.get("/objects/{object_id}", summary="Retrieve an object")
async def get_object(object_id: str, service: Service) -> Object:
    return await service.get(object_id)
