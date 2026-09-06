import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pymongo.errors import PyMongoError

from app.core.exceptions import ApplicationError
from app.db.mongodb import MongoDB, get_mongodb

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class DatabaseHealthResponse(HealthResponse):
    database: Literal["connected"] = "connected"


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/health/database")
async def database_health(
    mongodb: Annotated[MongoDB, Depends(get_mongodb)],
) -> DatabaseHealthResponse:
    try:
        await mongodb.ping()
    except PyMongoError:
        logger.error("MongoDB health check failed")
        raise ApplicationError(
            "DATABASE_UNAVAILABLE", "Database is unavailable.", 503
        ) from None
    return DatabaseHealthResponse()
