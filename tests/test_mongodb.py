import asyncio
from unittest.mock import AsyncMock, patch

from app.core.config import Settings
from app.db.mongodb import MongoDB


def test_indexes_are_repeated_without_dropping_data():
    with patch("app.db.mongodb.AsyncMongoClient") as factory:
        collections = {
            name: AsyncMock() for name in ("locations", "objects", "sources")
        }
        factory.return_value.__getitem__.return_value = collections
        database = MongoDB(Settings(mongodb_uri="mongodb://localhost:27017"))
        asyncio.run(database.ensure_indexes())
        asyncio.run(database.ensure_indexes())
        collections["locations"].create_index.assert_awaited_with("slug", unique=True)
        for name in ("objects", "sources"):
            collections[name].create_index.assert_awaited_with("location_id")
        for collection in collections.values():
            assert collection.create_index.await_count == 2
            collection.drop.assert_not_called()
