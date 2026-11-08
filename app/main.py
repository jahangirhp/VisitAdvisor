import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.router import router as api_router
from app.api.routes.health import router as health_router
from app.core.config import Settings
from app.core.exceptions import ApplicationError, register_exception_handlers
from app.core.logging import configure_logging
from app.db.mongodb import MongoDB
from app.modules.admin.assets import AdminStaticFiles
from app.modules.admin.assets import router as capture_assets_router
from app.modules.admin.server_capture import CaptureAnalyzer
from app.modules.live_camera.server import create_camera_app
from app.modules.recognition.limits import ImageUploadLimit

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Application startup")
    try:
        settings = Settings()
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
        logger.error("Invalid application configuration: %s", fields)
        raise RuntimeError(f"Invalid application configuration: {fields}") from None

    mongodb: MongoDB | None = None
    camera_preload: asyncio.Task[None] | None = None
    try:
        try:
            mongodb = MongoDB(settings)
            await mongodb.ping()
        except Exception:
            logger.error("MongoDB connection failed; check configuration and access")
            raise RuntimeError("MongoDB connection failed") from None
        try:
            await mongodb.ensure_indexes()
        except Exception:
            logger.error("MongoDB index initialization failed")
            raise RuntimeError("MongoDB index initialization failed") from None
        app.state.mongodb = mongodb
        app.state.settings = settings
        # Mounted applications do not receive a separate ASGI lifespan. Share
        # the parent client, whose shutdown is owned by this lifespan.
        app.state.camera.state.mongodb = mongodb
        if (
            settings.live_camera_preload
            and settings.live_camera_location_id
            and settings.live_camera_object_ids
        ):
            camera_preload = asyncio.create_task(app.state.camera.state.prepare())
        if settings.visual_enabled:
            from app.modules.visual.features import DinoEncoder, VisualFailure

            try:
                app.state.visual_encoder = await asyncio.to_thread(DinoEncoder)
                app.state.visual_lock = asyncio.Lock()
            except VisualFailure:
                raise RuntimeError(
                    "Visual model unavailable; run visual model setup"
                ) from None
        if settings.admin_capture_server_enabled:
            capture_analyzer = CaptureAnalyzer(settings.admin_capture_model_directory)
            app.state.admin_capture_analyzer = capture_analyzer
            try:
                # Cloud Run startup CPU is available here. Keep only model
                # weights resident afterward, so taking a photo avoids imports.
                await capture_analyzer.prepare()
            except ApplicationError:
                logger.warning(
                    "Capture model warmup unavailable; photo capture remains available"
                )
        logger.info("MongoDB connection successful")
        yield
    finally:
        analyzer = getattr(app.state, "admin_capture_analyzer", None)
        if analyzer is not None:
            analyzer.close()
            app.state.admin_capture_analyzer = None
        if camera_preload is not None:
            camera_preload.cancel()
            try:
                await camera_preload
            except asyncio.CancelledError:
                pass
        app.state.visual_geometry_cache.clear()
        app.state.camera.state.references = None
        app.state.camera.state.mongodb = None
        if mongodb is not None:
            await mongodb.close()
        logger.info("Application shutdown")


def create_app() -> FastAPI:
    application = FastAPI(title="VisitAdvisor", version="0.1.0", lifespan=lifespan)
    application.add_middleware(ImageUploadLimit)
    application.state.admin_preparation_lock = asyncio.Lock()
    application.state.visual_geometry_cache = {}
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)
    application.include_router(capture_assets_router)
    application.state.camera = create_camera_app()
    application.mount("/live", application.state.camera)
    application.mount(
        "/admin",
        AdminStaticFiles(
            directory=Path(__file__).parent / "modules/admin/static", html=True
        ),
        name="admin",
    )
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)
