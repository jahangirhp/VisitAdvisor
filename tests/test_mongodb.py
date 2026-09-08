import asyncio
from unittest.mock import AsyncMock, patch

from app.core.config import Settings
from app.db.mongodb import MongoDB


def test_indexes_are_repeated_without_dropping_data():
    with patch("app.db.mongodb.AsyncMongoClient") as factory:
        collections = {
            name: AsyncMock()
            for name in ("locations", "objects", "sources", "knowledge_chunks")
        }
        factory.return_value.__getitem__.return_value = collections
        database = MongoDB(Settings(mongodb_uri="mongodb://localhost:27017"))
        asyncio.run(database.ensure_indexes())
        asyncio.run(database.ensure_indexes())
        collections["locations"].create_index.assert_awaited_with("slug", unique=True)
        for name in ("objects", "sources"):
            collections[name].create_index.assert_any_await("location_id")
            collections[name].create_index.assert_any_await(
                [("location_id", 1), ("import_provider", 1), ("external_id", 1)],
                unique=True,
                partialFilterExpression={
                    "import_provider": {"$type": "string"},
                    "external_id": {"$type": "string"},
                },
                name="unique_import_identity",
            )
        assert collections["locations"].create_index.await_count == 2
        for name in ("objects", "sources"):
            assert collections[name].create_index.await_count == 4
        for collection in collections.values():
            collection.drop.assert_not_called()
