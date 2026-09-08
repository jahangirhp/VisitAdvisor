from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request

from app.core.config import Settings
from app.db.mongodb import get_database
from app.modules.retrieval.models import (
    MatchRequest,
    MatchResponse,
    SearchRequest,
    SearchResponse,
)
from app.modules.retrieval.provider import OpenAIEmbedder
from app.modules.retrieval.repository import RetrievalRepository
from app.modules.retrieval.service import RetrievalService

router = APIRouter(tags=["retrieval"])


async def get_service(request: Request) -> AsyncIterator[RetrievalService]:
    settings: Settings = request.app.state.settings
    key = settings.openai_api_key
    headers = {"Authorization": "Bearer " + key.get_secret_value()} if key else {}
    async with httpx.AsyncClient(timeout=8, headers=headers) as http:
        embedder = (
            OpenAIEmbedder(
                http, settings.embedding_model, settings.embedding_dimensions
            )
            if key and key.get_secret_value().strip()
            else None
        )
        yield RetrievalService(
            RetrievalRepository(get_database(request), settings), embedder
        )


Service = Annotated[RetrievalService, Depends(get_service)]


@router.post("/locations/{location_id}/search", summary="Search within a museum")
async def search(
    location_id: str, data: SearchRequest, service: Service
) -> SearchResponse:
    return await service.search(location_id, data)


@router.post("/recognition/match", summary="Match already-extracted OCR text")
async def match(data: MatchRequest, service: Service) -> MatchResponse:
    return await service.match(data.location_id, data)
