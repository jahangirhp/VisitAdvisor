"""One async client per application lifespan; no collections or indexes yet."""

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
        )
        self.database = self.client[settings.mongodb_database]

    async def ping(self) -> None:
        await self.database.command("ping")

    async def close(self) -> None:
        await self.client.close()


def get_mongodb(request: Request) -> MongoDB:
    return cast(MongoDB, request.app.state.mongodb)


def get_database(request: Request) -> AsyncDatabase[dict[str, Any]]:
    """Database dependency for future domain repositories."""
    return get_mongodb(request).database
