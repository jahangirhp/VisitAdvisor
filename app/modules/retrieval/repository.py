from typing import Any

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.operations import SearchIndexModel

from app.core.config import Settings
from app.modules.retrieval.provider import RetrievalFailure
from app.modules.retrieval.text import MAX_CORPUS, TEXT_VERSION

# Keep large generated stories/raw evidence out of the lexical scan.
PROJECTION = dict.fromkeys(
    (
        "location_id",
        "name",
        "aliases",
        "creator",
        "year",
        "type",
        "metadata",
        "external_id",
        "description",
        "source_ids",
        "prepared_content.generated_at",
    ),
    1,
)


def index_definition(dimensions: int) -> dict[str, Any]:
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dimensions,
                "similarity": "cosine",
            },
            *(
                {"type": "filter", "path": key}
                for key in (
                    "location_id",
                    "embedding_model",
                    "embedding_dimensions",
                    "text_version",
                    "content_type",
                )
            ),
        ]
    }


class RetrievalRepository:
    def __init__(self, db: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.chunks = db["knowledge_chunks"]

    async def objects(self, location_id: str) -> list[dict[str, Any]]:
        docs = (
            await self.db["objects"]
            .find({"location_id": location_id}, PROJECTION)
            .sort("_id", 1)
            .limit(MAX_CORPUS + 1)
            .to_list()
        )
        if len(docs) > MAX_CORPUS:
            raise RetrievalFailure("MUSEUM_EXCEEDS_MVP_LIMIT")
        return docs

    async def get_object(
        self, location_id: str, object_id: str
    ) -> dict[str, Any] | None:
        return await self.db["objects"].find_one(
            {"_id": ObjectId(object_id), "location_id": location_id}, PROJECTION
        )

    async def source_ids(self, location_id: str) -> set[str]:
        return {
            str(doc["_id"])
            async for doc in self.db["sources"].find(
                {"location_id": location_id}, {"_id": 1}
            )
        }

    async def index_status(self, create: bool = False) -> str:
        name = self.settings.vector_index_name
        indexes = await (await self.chunks.list_search_indexes(name)).to_list()
        expected = index_definition(self.settings.embedding_dimensions)
        if not indexes:
            if not create:
                return "missing"
            await self.chunks.create_search_index(
                SearchIndexModel(definition=expected, name=name, type="vectorSearch")
            )
            return "building"
        index = indexes[0]
        actual = index.get("latestDefinition", {})

        # Atlas may reorder field definitions.
        def fields(definition: dict[str, Any]) -> list[dict[str, Any]]:
            return sorted(
                definition.get("fields", []), key=lambda field: field.get("path", "")
            )

        if index.get("type") != "vectorSearch" or fields(actual) != fields(expected):
            raise RetrievalFailure("VECTOR_INDEX_CONFIGURATION_MISMATCH")
        return (
            "ready"
            if index.get("queryable") and index.get("status") == "READY"
            else "building"
        )

    def vector_filter(self, location_id: str) -> dict[str, Any]:
        return {
            "location_id": location_id,
            "embedding_model": self.settings.embedding_model,
            "embedding_dimensions": self.settings.embedding_dimensions,
            "text_version": TEXT_VERSION,
            "content_type": "identity",
        }

    async def embedding_count(self, location_id: str) -> int:
        return await self.chunks.count_documents(self.vector_filter(location_id))

    async def vector_search(
        self, location_id: str, vector: list[float]
    ) -> list[dict[str, Any]]:
        filters = self.vector_filter(location_id)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.settings.vector_index_name,
                    "path": "embedding",
                    "queryVector": vector,
                    "filter": filters,
                    "numCandidates": 400,
                    "limit": 20,
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "object_id": 1,
                    "location_id": 1,
                    "content_hash": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        return await (await self.chunks.aggregate(pipeline, maxTimeMS=5000)).to_list()
