from fastapi import APIRouter

from app.modules.locations.router import router as locations_router
from app.modules.objects.router import router as objects_router
from app.modules.sources.router import router as sources_router

router = APIRouter(prefix="/api/v1")
router.include_router(locations_router)
router.include_router(objects_router)
router.include_router(sources_router)
