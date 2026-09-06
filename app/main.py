import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from pydantic import ValidationError

from app.api.router import router as api_router
from app.api.routes.health import router as health_router
from app.core.config import Settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.db.mongodb import MongoDB

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
    try:
        try:
            mongodb = MongoDB(settings)
            await mongodb.ping()
        except Exception:
            logger.error("MongoDB connection failed; check configuration and access")
            raise RuntimeError("MongoDB connection failed") from None
        app.state.mongodb = mongodb
        app.state.settings = settings
        logger.info("MongoDB connection successful")
        yield
    finally:
        if mongodb is not None:
            await mongodb.close()
        logger.info("Application shutdown")


def create_app() -> FastAPI:
    application = FastAPI(title="VisitAdvisor", version="0.1.0", lifespan=lifespan)
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings()
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port)
