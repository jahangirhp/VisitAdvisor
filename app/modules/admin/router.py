"""Token-protected administration; no credentials are embedded in the UI."""

import asyncio
import secrets
from typing import Annotated, Any, Literal, cast

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.core.domain import NonBlank, validate_id
from app.core.exceptions import ApplicationError
from app.db.mongodb import get_database
from app.modules.admin.service import PhotoService
from app.modules.locations.models import Location, LocationCreate, LocationData
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.objects.models import Object, ObjectCreate
from app.modules.objects.repository import ObjectRepository
from app.modules.objects.service import ObjectService
from app.modules.recognition.images import MAX_IMAGE_BYTES
from app.modules.sources.models import SourceCreate
from app.modules.sources.repository import SourceRepository
from app.modules.visual.features import DinoEncoder, ImageEncoder, VisualFailure


def require_admin(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    configured = request.app.state.settings.admin_token
    if configured is None or not configured.get_secret_value():
        raise ApplicationError("ADMIN_DISABLED", "Admin access is not configured.", 503)
    expected = "Bearer " + configured.get_secret_value()
    if not authorization or not secrets.compare_digest(
        authorization.encode(), expected.encode()
    ):
        raise ApplicationError("ADMIN_UNAUTHORIZED", "Enter a valid admin token.", 401)


router = APIRouter(
    prefix="/admin", tags=["admin photos"], dependencies=[Depends(require_admin)]
)
Database = Annotated[AsyncDatabase[dict[str, Any]], Depends(get_database)]


def service(request: Request, database: Database) -> PhotoService:
    settings = request.app.state.settings
    return PhotoService(
        database,
        settings.admin_planar_photo_target,
        settings.admin_multiview_photo_target,
    )


Service = Annotated[PhotoService, Depends(service)]


class PhotoMetadata(BaseModel):
    viewpoint: str = Field(min_length=1, max_length=300)
    photographer: str = Field(min_length=1, max_length=200)
    capture_group: str = Field(min_length=1, max_length=200)
    rights: str = Field(min_length=1, max_length=1000)
    source_id: str


class ObjectObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    observer: str = Field(min_length=1, max_length=200)
    reviewed: Literal[True]


class AdminObjectCreate(ObjectCreate):
    observation: ObjectObservation | None = None


class AdminVenueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: NonBlank = Field(max_length=300)


@router.post("/venues", status_code=201)
async def create_venue(
    data: LocationCreate | AdminVenueCreate, database: Database
) -> Location:
    repository = LocationRepository(database)
    if isinstance(data, LocationCreate):
        return await LocationService(repository).create(data)
    return await repository.create(
        LocationData(name=data.name, slug=f"venue-{ObjectId()}", type="other")
    )


@router.get("/venues")
async def venues(
    database: Database, offset: Annotated[int, Query(ge=0)] = 0
) -> list[dict[str, Any]]:
    rows = (
        await database["locations"]
        .find({}, {"name": 1, "city": 1})
        .sort("_id", 1)
        .skip(offset)
        .limit(100)
        .to_list()
    )
    return [
        {"id": str(row["_id"]), "name": row["name"], "city": row.get("city", "")}
        for row in rows
    ]


@router.get("/venues/{location_id}/objects")
async def objects(
    location_id: str,
    service: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> dict[str, Any]:
    return await service.inventory(validate_id(location_id), offset, limit)


@router.post("/venues/{location_id}/objects", status_code=201)
async def create_object(
    location_id: str, data: AdminObjectCreate, database: Database
) -> Object:
    locations = LocationService(LocationRepository(database))
    sources = SourceRepository(database)
    catalog_data = ObjectCreate.model_validate(data.model_dump(exclude={"observation"}))
    # A manual observation is the operator's assertion, never an official museum
    # or generated source. Existing catalog validation still checks every link.
    location = await locations.get(location_id)
    if data.observation is not None:
        ids = list({validate_id(value) for value in catalog_data.source_ids})
        if ids and await sources.count_for_location(ids, location.id) != len(ids):
            raise ApplicationError(
                "INVALID_SOURCE_REFERENCE", "Sources must belong to this venue.", 422
            )
        observer = data.observation.observer.strip()
        if not observer:
            raise ApplicationError("REVIEW_REQUIRED", "Enter your name.", 422)
        observation = await sources.create(
            SourceCreate(
                title=f"On-site identification: {data.name}",
                type="manual",
                publisher=observer,
            ),
            location.id,
        )
        catalog_data.source_ids = [*ids, observation.id]
    return await ObjectService(
        ObjectRepository(database),
        locations,
        sources,
    ).create(location.id, catalog_data)


@router.get("/venues/{location_id}/objects/{object_id}")
async def detail(location_id: str, object_id: str, service: Service) -> dict[str, Any]:
    return await service.detail(validate_id(location_id), validate_id(object_id))


@router.post("/venues/{location_id}/objects/{object_id}/photos", status_code=201)
async def upload(
    location_id: str,
    object_id: str,
    service: Service,
    image: Annotated[UploadFile, File()],
    viewpoint: Annotated[str, Form(min_length=1, max_length=300)],
    photographer: Annotated[str, Form(min_length=1, max_length=200)],
    capture_group: Annotated[str, Form(min_length=1, max_length=200)],
    rights: Annotated[str, Form(min_length=1, max_length=1000)],
    reviewed: Annotated[bool | None, Form()] = None,
    source_id: Annotated[str, Form()] = "",
) -> dict[str, str]:
    try:
        if reviewed is False or any(
            not value.strip()
            for value in (viewpoint, photographer, capture_group, rights)
        ):
            raise ApplicationError(
                "REVIEW_REQUIRED",
                "Complete the photo details or retry the upload.",
                422,
            )
        data = await image.read(MAX_IMAGE_BYTES + 1)
        metadata = PhotoMetadata(
            viewpoint=viewpoint.strip(),
            photographer=photographer.strip(),
            capture_group=capture_group.strip(),
            rights=rights.strip(),
            source_id=validate_id(source_id) if source_id else "",
        )
        photo_id = await service.upload(
            validate_id(location_id),
            validate_id(object_id),
            data,
            image.content_type,
            metadata.model_dump(),
        )
        return {"id": photo_id, "status": "saved_for_preparation"}
    finally:
        await image.close()


@router.get("/venues/{location_id}/objects/{object_id}/photos/{photo_id}")
async def photo(
    location_id: str, object_id: str, photo_id: str, service: Service
) -> Response:
    row = await service.photos.find_one(
        {
            "_id": ObjectId(validate_id(photo_id)),
            "location_id": validate_id(location_id),
            "object_id": validate_id(object_id),
        }
    )
    if row is None:
        raise ApplicationError("PHOTO_NOT_FOUND", "Photo not found.", 404)
    # Serve only validated, metadata-free JPEGs; never raw EXIF/GPS data.
    from app.modules.recognition.images import prepare_image

    image = await asyncio.to_thread(
        prepare_image, bytes(row["data"]), row["content_type"]
    )
    return Response(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/venues/{location_id}/objects/{object_id}/photos/{photo_id}", status_code=204
)
async def remove_photo(
    location_id: str, object_id: str, photo_id: str, request: Request, service: Service
) -> Response:
    location_id, object_id, photo_id = (
        validate_id(location_id),
        validate_id(object_id),
        validate_id(photo_id),
    )
    lock = request.app.state.admin_preparation_lock
    if lock.locked():
        raise ApplicationError(
            "PREPARATION_BUSY", "Preparation is running; retry shortly.", 409
        )
    async with lock:
        current = await service.visual.current(location_id, object_id)
        if any(
            v.get("reference", {}).get("evidence", {}).get("upload_id") == photo_id
            for v in (current or {}).get("views", [])
        ):
            raise ApplicationError(
                "PHOTO_IN_USE", "This photo is already published as a reference.", 409
            )
        result = await service.photos.delete_one(
            {
                "_id": ObjectId(photo_id),
                "location_id": location_id,
                "object_id": object_id,
            }
        )
        if not result.deleted_count:
            raise ApplicationError("PHOTO_NOT_FOUND", "Photo not found.", 404)
        return Response(status_code=204)


@router.post("/venues/{location_id}/objects/{object_id}/prepare")
async def prepare(
    location_id: str, object_id: str, request: Request, service: Service
) -> dict[str, Any]:
    # One foreground preparation at a time, with no unbounded background queue.
    lock = request.app.state.admin_preparation_lock
    if lock.locked():
        raise ApplicationError(
            "PREPARATION_BUSY", "Another preparation is running; retry shortly.", 409
        )
    async with lock:
        try:
            encoder = getattr(request.app.state, "admin_encoder", None)
            if encoder is None:
                encoder = await asyncio.to_thread(DinoEncoder)
                request.app.state.admin_encoder = encoder
            return await service.publish(
                validate_id(location_id),
                validate_id(object_id),
                cast(ImageEncoder, encoder),
            )
        except VisualFailure:
            raise ApplicationError(
                "MODEL_UNAVAILABLE",
                "Install visual dependencies and cached model weights first.",
                503,
            ) from None
