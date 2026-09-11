from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from starlette.concurrency import run_in_threadpool

from app.core.exceptions import ApplicationError
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.objects.repository import ObjectRepository
from app.modules.recognition.images import prepare_image
from app.modules.recognition.models import RecognitionResponse
from app.modules.recognition.provider import OCRReader
from app.modules.retrieval.models import SearchRequest
from app.modules.retrieval.service import RetrievalService
from app.modules.retrieval.text import normalize


class RecognitionService:
    def __init__(
        self,
        db: AsyncDatabase[dict[str, Any]],
        matcher: RetrievalService,
        reader: OCRReader | None,
    ) -> None:
        self.db, self.matcher, self.reader = db, matcher, reader

    async def recognize(
        self, location_id: str, data: bytes, content_type: str | None
    ) -> RecognitionResponse:
        # Validate the museum before any paid work, even for blank images.
        location = await LocationService(LocationRepository(self.db)).get(location_id)
        image = await run_in_threadpool(prepare_image, data, content_type)
        if self.reader is None:
            raise ApplicationError(
                "OCR_NOT_CONFIGURED", "Text recognition is not configured.", 503
            )
        ocr = await self.reader.extract(image)
        if ocr.quality in ("no_text", "unreadable") or not normalize(ocr.text):
            return RecognitionResponse(
                location_id=location.id,
                status="unreadable" if ocr.quality == "unreadable" else "no_text",
                ocr=ocr,
                warnings=["RESCAN_LABEL"],
            )
        match = await self.matcher.match(location.id, SearchRequest(text=ocr.text))
        warnings = list(match.warnings)
        for condition, code in (
            (ocr.quality == "partial", "OCR_PARTIAL"),
            (ocr.multiple_labels, "OCR_MULTIPLE_LABELS"),
            (ocr.truncated, "OCR_TEXT_TRUNCATED"),
        ):
            if condition:
                warnings.append(code)
        if (
            ocr.quality == "partial" or ocr.multiple_labels or ocr.truncated
        ) and match.status == "matched":
            match = match.model_copy(
                update={"status": "ambiguous", "matched_object_id": None}
            )
        obj = None
        if match.status == "matched" and match.matched_object_id:
            obj = await ObjectRepository(self.db).get(match.matched_object_id)
            if obj is None or obj.location_id != location.id:
                raise ApplicationError(
                    "MATCH_CHANGED", "Object changed. Please scan again.", 409
                )
            candidate = next(c for c in match.candidates if c.object_id == obj.id)
            names = {normalize(name) for name in [obj.name, *obj.aliases]}
            # A title mentioned only in a paragraph is not a primary identity.
            # Preserve qualifiers: "Copy of The Night Watch" is not "The Night Watch".
            title_confirmed = any(normalize(title) in names for title in ocr.titles)
            if not title_confirmed and (
                ocr.titles or "identifier_exact" not in candidate.signals
            ):
                match = match.model_copy(
                    update={"status": "ambiguous", "matched_object_id": None}
                )
                warnings.append("OCR_IDENTITY_UNCONFIRMED")
                obj = None
            else:
                warnings.append(
                    "CONTENT_UNREVIEWED"
                    if obj.prepared_content
                    else "CONTENT_NOT_PREPARED"
                )
        return RecognitionResponse(
            location_id=location.id,
            status=match.status,
            ocr=ocr,
            match=match,
            object=obj,
            warnings=warnings,
        )
