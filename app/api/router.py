from fastapi import APIRouter

from app.modules.admin.router import router as admin_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.locations.router import router as locations_router
from app.modules.objects.router import router as objects_router
from app.modules.recognition.router import router as recognition_router
from app.modules.retrieval.router import router as retrieval_router
from app.modules.sources.router import router as sources_router

router = APIRouter(prefix="/api/v1")
router.include_router(admin_router)
router.include_router(knowledge_router)
router.include_router(locations_router)
router.include_router(objects_router)
router.include_router(sources_router)

router.include_router(retrieval_router)

router.include_router(recognition_router)
