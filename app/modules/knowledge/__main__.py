"""Bounded foreground content preparation; no API or background job trigger."""

import argparse
import asyncio
import json
import logging

import httpx
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.domain import validate_id
from app.core.exceptions import ApplicationError
from app.core.logging import configure_logging
from app.db.mongodb import MongoDB
from app.modules.knowledge.provider import GenerationFailure, OpenAIContentGenerator
from app.modules.knowledge.service import KnowledgeService
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService

logger = logging.getLogger("app.modules.knowledge")


async def run(location_id: str, limit: int, offset: int, force: bool) -> int:
    database: MongoDB | None = None
    report: dict[str, object] = {"generated": 0, "skipped": 0, "failed": 0}
    try:
        settings = Settings()
        if (
            not settings.openai_api_key
            or not settings.openai_api_key.get_secret_value().strip()
        ):
            raise GenerationFailure("OPENAI_API_KEY_REQUIRED")
        validate_id(location_id)
        database = MongoDB(settings)
        await database.ping()
        location = await LocationService(LocationRepository(database.database)).get(
            location_id
        )
        objects = (
            await database.database["objects"]
            .find(
                {
                    "location_id": location.id,
                    "import_provider": {"$type": "string"},
                },
                {"_id": 1},
            )
            .sort("_id", 1)
            .skip(offset)
            .limit(limit)
            .to_list()
        )
        errors: list[dict[str, str]] = []
        report.update(location_id=location.id, attempted=len(objects), errors=errors)
        service = KnowledgeService(database.database)
        async with httpx.AsyncClient(
            timeout=90,
            headers={
                "Authorization": "Bearer " + settings.openai_api_key.get_secret_value(),
            },
        ) as http:
            generator = OpenAIContentGenerator(http, settings.llm_model)

            async def prepare(object_id: str) -> tuple[str, str | None]:
                try:
                    return await service.prepare(object_id, generator, force), None
                except (GenerationFailure, PyMongoError, ValidationError) as exc:
                    code = (
                        str(exc)
                        if isinstance(exc, GenerationFailure)
                        else "PREPARATION_FAILED"
                    )
                    return "failed", code

            # Three requests at a time, within this CLI process only.
            for start in range(0, len(objects), 3):
                batch = objects[start : start + 3]
                results = await asyncio.gather(
                    *(prepare(str(obj["_id"])) for obj in batch)
                )
                for obj, (outcome, code) in zip(batch, results, strict=True):
                    report[outcome] = int(str(report[outcome])) + 1
                    if code:
                        errors.append({"object_id": str(obj["_id"]), "code": code})
                logger.info(
                    "Content preparation: %s/%s processed",
                    start + len(batch),
                    len(objects),
                )
                if any(
                    code
                    in {
                        "PROVIDER_ACCESS_DENIED",
                        "PROVIDER_QUOTA_EXHAUSTED",
                        "PROVIDER_REQUEST_FAILED",
                    }
                    for _, code in results
                ):
                    report["attempted"] = start + len(batch)
                    report["aborted"] = True
                    break
        print(json.dumps(report, indent=2))
        return 1 if errors else 0
    except (GenerationFailure, ApplicationError, ValidationError, PyMongoError) as exc:
        code = (
            str(exc)
            if isinstance(exc, GenerationFailure)
            else exc.code
            if isinstance(exc, ApplicationError)
            else "CONFIGURATION_OR_DATABASE_ERROR"
        )
        print(json.dumps({"error": {"code": code}}))
        return 1
    finally:
        if database is not None:
            await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare visitor content for imported objects."
    )
    parser.add_argument("--location-id", required=True)
    parser.add_argument(
        "--limit", type=int, default=3, help="Objects to attempt (1–100; default 3)"
    )
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if evidence/model/prompt are unchanged",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 100 or args.offset < 0:
        parser.error("limit must be 1–100 and offset must be nonnegative")
    configure_logging()
    raise SystemExit(
        asyncio.run(run(args.location_id, args.limit, args.offset, args.force))
    )


if __name__ == "__main__":
    main()
