"""One async client per application lifespan and idempotent domain indexes."""

from typing import Any, cast

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import Settings


class MongoDB:
    def __init__(self, settings: Settings) -> None:
        self.client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            settings.mongodb_uri.get_secret_value(),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            timeoutMS=5000,
            tz_aware=True,
        )
        self.database = self.client[settings.mongodb_database]

    async def ensure_indexes(self) -> None:
        await self.database["locations"].create_index("slug", unique=True)
        await self.database["objects"].create_index("location_id")
        await self.database["sources"].create_index("location_id")

        for collection in ("objects", "sources"):
            await self.database[collection].create_index(
                [("location_id", 1), ("import_provider", 1), ("external_id", 1)],
                unique=True,
                partialFilterExpression={
                    "import_provider": {"$type": "string"},
                    "external_id": {"$type": "string"},
                },
                name="unique_import_identity",
            )

    async def ping(self) -> None:
        await self.database.command("ping")

    async def close(self) -> None:
        await self.client.close()


def get_mongodb(request: Request) -> MongoDB:
    return cast(MongoDB, request.app.state.mongodb)


def get_database(request: Request) -> AsyncDatabase[dict[str, Any]]:
    """Database dependency for future domain repositories."""
    return get_mongodb(request).database
