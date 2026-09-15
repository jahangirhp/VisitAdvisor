"""Opt-in candidate preview. Automatic visual selection is deliberately disabled."""

import asyncio
import io
from typing import Literal

from PIL import Image, ImageStat
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from app.core.exceptions import ApplicationError
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.recognition.images import prepare_image
from app.modules.visual.features import ImageEncoder, VisualFailure
from app.modules.visual.repository import VisualRepository


class VisualCandidate(BaseModel):
    object_id: str
    name: str
    type: str
    score: float
    content_available: bool
    source_ids: list[str]
    warnings: list[str]


class VisualResponse(BaseModel):
    version: Literal["visual-pilot-v1"] = "visual-pilot-v1"
    method: Literal["visual_embeddings"] = "visual_embeddings"
    location_id: str
    status: Literal["ambiguous", "no_match", "unreadable"]
    ocr: None = None
    object: None = None
    candidates: list[VisualCandidate] = Field(default_factory=list)
    covered_objects: int
    total_objects: int
    warnings: list[str]


class VisualService:
    def __init__(
        self, repository: VisualRepository, encoder: ImageEncoder, lock: asyncio.Lock
    ) -> None:
        self.repository, self.encoder, self.lock = repository, encoder, lock

    async def recognize(
        self, location_id: str, data: bytes, content_type: str | None
    ) -> VisualResponse:
        location = await LocationService(LocationRepository(self.repository.db)).get(
            location_id
        )
        clean = await asyncio.to_thread(prepare_image, data, content_type)
        warnings = ["VISUAL_UNCALIBRATED", "SELECT_CANDIDATE_OR_RESCAN"]
        try:

            async def check_index() -> str | VisualFailure | PyMongoError:
                try:
                    return await self.repository.index_status()
                except (VisualFailure, PyMongoError) as exc:
                    return exc

            # These fresh reads are independent. Keep inference behind both gates,
            # and keep missing/featureless-image responses independent of the index.
            (records, objects), index_state = await asyncio.gather(
                self.repository.active_snapshot(
                    location.id,
                    self.encoder.model,
                    self.encoder.preprocessing,
                    include_features=False,
                ),
                check_index(),
            )
            total = len(objects)
            if len(records) < total:
                warnings.append("VISUAL_REFERENCE_COVERAGE_PARTIAL")
            response = VisualResponse(
                location_id=location.id,
                status="no_match",
                covered_objects=len(records),
                total_objects=total,
                warnings=warnings,
            )
            if not records:
                response.warnings.append("VISUAL_REFERENCES_MISSING")
                return response
            with Image.open(io.BytesIO(clean)) as image:
                if max(ImageStat.Stat(image).stddev) < 2:
                    response.status = "unreadable"
                    return response
            if isinstance(index_state, (VisualFailure, PyMongoError)):
                raise index_state
            if index_state != "ready":
                raise VisualFailure("VISUAL_INDEX_NOT_READY")
            if self.lock.locked():
                raise VisualFailure("VISUAL_BUSY")
            async with self.lock:
                query = await asyncio.to_thread(
                    self.encoder.describe, clean, regions=False
                )
            ranked = await self.repository.search(
                location.id,
                query["regions"][0]["embedding"],
                records,
                self.encoder.model,
                self.encoder.preprocessing,
            )
            # Revalidate evidence/location after matching and concurrent refreshes.
            active, objects = await self.repository.active_snapshot(
                location.id,
                self.encoder.model,
                self.encoder.preprocessing,
                include_features=False,
            )
            active_sets = {r["set_id"] for r in active}
            if active_sets != {r["set_id"] for r in records}:
                response.warnings.append("VISUAL_REFERENCE_SET_CHANGED")
                return response
            allowed = {r["object_id"] for r in records if r["set_id"] in active_sets}
            for candidate in ranked:
                obj = objects.get(candidate["object_id"])
                # This is a provisional retrieval floor, never an identity threshold.
                if (
                    obj is None
                    or candidate["object_id"] not in allowed
                    or candidate["score"] < 0.45
                ):
                    continue
                content = obj.get("prepared_content")
                response.candidates.append(
                    VisualCandidate(
                        object_id=candidate["object_id"],
                        name=obj["name"],
                        type=obj["type"],
                        score=candidate["score"],
                        source_ids=obj.get("source_ids", []),
                        content_available=bool(content),
                        warnings=[
                            "CONTENT_UNREVIEWED" if content else "CONTENT_NOT_PREPARED"
                        ],
                    )
                )
            if response.candidates:
                response.status = "ambiguous"
            return response
        except (VisualFailure, PyMongoError) as exc:
            code = (
                str(exc)
                if isinstance(exc, VisualFailure)
                else "VISUAL_DATABASE_UNAVAILABLE"
            )
            raise ApplicationError(
                code, "Visual recognition is unavailable.", 503
            ) from None
