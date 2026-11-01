from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import get_database
from app.modules.recognition.images import MAX_IMAGE_BYTES
from app.modules.recognition.models import RecognitionResponse
from app.modules.recognition.provider import OpenAIOCR
from app.modules.recognition.service import RecognitionService
from app.modules.retrieval.router import Service as MatchingService
from app.modules.visual.repository import VisualRepository
from app.modules.visual.service import VisualResponse, VisualService

router = APIRouter(tags=["recognition"])


async def get_service(
    request: Request, matcher: MatchingService
) -> AsyncIterator[RecognitionService]:
    settings: Settings = request.app.state.settings
    key = settings.openai_api_key
    headers = {"Authorization": "Bearer " + key.get_secret_value()} if key else {}
    async with httpx.AsyncClient(
        timeout=settings.ocr_timeout_seconds, headers=headers
    ) as http:
        reader = (
            OpenAIOCR(http, settings.ocr_model)
            if key and key.get_secret_value().strip()
            else None
        )
        yield RecognitionService(get_database(request), matcher, reader)


@router.post(
    "/recognition/image", summary="Recognize a label or preview visual candidates"
)
async def recognize_image(
    request: Request,
    location_id: Annotated[str, Form(min_length=24, max_length=24)],
    image: Annotated[
        UploadFile, File(description="JPEG, PNG or WebP; up to 5 MiB and 16 megapixels")
    ],
    service: Annotated[RecognitionService, Depends(get_service)],
    mode: Annotated[Literal["ocr", "visual"], Form()] = "ocr",
) -> RecognitionResponse | VisualResponse:
    try:
        data = await image.read(MAX_IMAGE_BYTES + 1)
        if mode == "visual":
            if not getattr(request.app.state, "visual_encoder", None):
                raise ApplicationError(
                    "VISUAL_NOT_CONFIGURED", "Visual recognition is not enabled.", 503
                )
            visual = VisualService(
                VisualRepository(get_database(request)),
                request.app.state.visual_encoder,
                request.app.state.visual_lock,
                request.app.state.settings.visual_geometry_object_ids,
                request.app.state.visual_geometry_cache,
            )
            return await visual.recognize(location_id, data, image.content_type)
        return await service.recognize(location_id, data, image.content_type)
    finally:
        await image.close()
