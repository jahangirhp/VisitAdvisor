"""Run with python -m app.modules.ingestion --location-id ID --limit 100."""

import argparse
import asyncio
import json
import sys

import httpx
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.core.logging import configure_logging
from app.db.mongodb import MongoDB
from app.modules.ingestion.repository import ImportRepository
from app.modules.ingestion.rijksmuseum import RijksmuseumClient
from app.modules.ingestion.service import import_rijksmuseum
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService


def import_limit(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("limit must be between 1 and 500")
    return number


async def run(location_id: str, limit: int) -> int:
    database: MongoDB | None = None
    try:
        database = MongoDB(Settings())
        await database.ping()
        await database.ensure_indexes()
        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "VisitAdvisor/0.1 (bounded museum metadata import)"},
        ) as http:
            report = await import_rijksmuseum(
                location_id,
                limit,
                LocationService(LocationRepository(database.database)),
                RijksmuseumClient(http),
                ImportRepository(database.database),
            )
        print(report.model_dump_json(indent=2))
        return 1 if report.errors else 0
    except (ValidationError, PyMongoError, ApplicationError) as exc:
        code = (
            exc.code
            if isinstance(exc, ApplicationError)
            else (
                "INVALID_CONFIGURATION"
                if isinstance(exc, ValidationError)
                else "DATABASE_ERROR"
            )
        )
        print(json.dumps({"error": {"code": code}}))
        return 1
    finally:
        if database is not None:
            await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import paintings from the official Rijksmuseum API."
    )
    parser.add_argument(
        "--location-id", required=True, help="Existing Rijksmuseum location ID"
    )
    parser.add_argument(
        "--limit",
        type=import_limit,
        default=100,
        help="Maximum records to attempt (1–500; default 100)",
    )
    args = parser.parse_args()
    configure_logging()
    sys.exit(asyncio.run(run(args.location_id, args.limit)))


if __name__ == "__main__":
    main()
