"""Prepare identity embeddings and explicitly manage the Atlas vector index."""

import argparse
import asyncio
import json

import httpx
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import MongoDB
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.retrieval.preparation import prepare
from app.modules.retrieval.provider import OpenAIEmbedder, RetrievalFailure
from app.modules.retrieval.repository import RetrievalRepository


async def run(args: argparse.Namespace) -> int:
    database = None
    try:
        settings = Settings()
        database = MongoDB(settings)
        await database.ping()
        await database.ensure_indexes()
        repository = RetrievalRepository(database.database, settings)
        if args.command == "index":
            state = await repository.index_status(create=args.create)
            print(json.dumps({"index": settings.vector_index_name, "status": state}))
            return 0
        location = await LocationService(LocationRepository(database.database)).get(
            args.location_id
        )
        key = settings.openai_api_key
        if not key or not key.get_secret_value().strip():
            raise RetrievalFailure("EMBEDDING_NOT_CONFIGURED")
        async with httpx.AsyncClient(
            timeout=30, headers={"Authorization": "Bearer " + key.get_secret_value()}
        ) as http:
            embedder = OpenAIEmbedder(
                http,
                settings.embedding_model,
                settings.embedding_dimensions,
                attempts=3,
            )
            report = await prepare(
                repository, location.id, embedder, args.limit, args.offset
            )
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    except (RetrievalFailure, ApplicationError, ValidationError, PyMongoError) as exc:
        code = (
            str(exc)
            if isinstance(exc, RetrievalFailure)
            else (
                exc.code
                if isinstance(exc, ApplicationError)
                else "RETRIEVAL_SETUP_FAILED"
            )
        )
        print(json.dumps({"error": {"code": code}}))
        return 1
    finally:
        if database is not None:
            await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Check or create the Atlas vector index")
    index.add_argument("--create", action="store_true")
    preparation = commands.add_parser(
        "prepare", help="Prepare canonical identity embeddings"
    )
    preparation.add_argument("--location-id", required=True)
    preparation.add_argument("--limit", type=int, default=3)
    preparation.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    if args.command == "prepare" and (not 1 <= args.limit <= 500 or args.offset < 0):
        parser.error("limit must be 1–500 and offset must be nonnegative")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
