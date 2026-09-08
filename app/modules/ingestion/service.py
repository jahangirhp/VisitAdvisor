import logging

from pydantic import BaseModel, Field, ValidationError
from pymongo.errors import PyMongoError

from app.core.exceptions import ApplicationError
from app.modules.ingestion.repository import ImportRepository
from app.modules.ingestion.rijksmuseum import ImportFailure, RijksmuseumClient
from app.modules.locations.service import LocationService

logger = logging.getLogger(__name__)


class ImportReport(BaseModel):
    location_id: str
    requested: int
    attempted: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict[str, str]] = Field(default_factory=list)


async def import_rijksmuseum(
    location_id: str,
    limit: int,
    locations: LocationService,
    provider: RijksmuseumClient,
    repository: ImportRepository,
) -> ImportReport:
    if not 1 <= limit <= 500:
        raise ApplicationError(
            "INVALID_IMPORT_LIMIT", "Limit must be between 1 and 500.", 422
        )
    location = await locations.get(location_id)
    # Avoid accidentally attaching Rijksmuseum works to an unrelated destination.
    if location.slug != "rijksmuseum":
        raise ApplicationError(
            "IMPORT_LOCATION_MISMATCH",
            "This connector requires the rijksmuseum location.",
            422,
        )
    report = ImportReport(location_id=location.id, requested=limit)
    try:
        async for identifier in provider.identifiers(limit):
            report.attempted += 1
            try:
                record = await provider.record(identifier)
                outcome = await repository.save(location.id, record)
                setattr(report, outcome, getattr(report, outcome) + 1)
            except (ImportFailure, ValidationError, PyMongoError) as exc:
                code = (
                    str(exc)
                    if isinstance(exc, ImportFailure)
                    else (
                        "INVALID_RECORD"
                        if isinstance(exc, ValidationError)
                        else "DATABASE_WRITE_FAILED"
                    )
                )
                report.failed += 1
                report.errors.append({"external_id": identifier, "code": code})
            if report.attempted % 10 == 0:
                logger.info(
                    "Import progress: %s processed, %s failed",
                    report.attempted,
                    report.failed,
                )
    except ImportFailure as exc:
        report.errors.append({"code": str(exc)})
    return report
