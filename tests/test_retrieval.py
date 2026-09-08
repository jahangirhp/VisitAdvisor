"""Phase 4 behavioral tests: no network, real museum-shaped records."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pymongo.errors import OperationFailure

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.main import create_app
from app.modules.retrieval.models import MatchRequest, SearchRequest
from app.modules.retrieval.preparation import prepare
from app.modules.retrieval.provider import (
    EmbeddingBatch,
    OpenAIEmbedder,
    RetrievalFailure,
    validate_vector,
)
from app.modules.retrieval.repository import RetrievalRepository, index_definition
from app.modules.retrieval.service import RetrievalService
from app.modules.retrieval.text import (
    fingerprint,
    lexical_score,
    normalize,
    search_text,
)
from tests.test_domain import LOCATION, Collection, Cursor

L1, L2, S1 = (str(ObjectId()) for _ in range(3))
NOW = datetime.now(UTC)


def object_doc(name="The Night Watch", **changes):
    return {
        "_id": ObjectId(),
        "location_id": L1,
        "name": name,
        "type": "painting",
        "aliases": ["De Nachtwacht"],
        "creator": "Rembrandt van Rijn",
        "year": 1642,
        "description": "A group portrait of a civic guard company.",
        "source_ids": [S1],
        "metadata": {"rijksmuseum": {"object_number": "SK-C-5"}},
        "created_at": NOW,
        "updated_at": NOW,
        **changes,
    }


class RetrievalCursor(Cursor):
    async def to_list(self):
        return deepcopy(self.documents)


class RetrievalCollection(Collection):
    def find(self, query, projection=None):
        return RetrievalCursor([d for d in self.documents if self.matches(d, query)])

    async def find_one(self, query, projection=None):
        return await super().find_one(query)

    async def update_one(self, query, update, upsert=False):
        doc = next((d for d in self.documents if self.matches(d, query)), None)
        if doc is None:
            assert upsert
            doc = {
                "_id": ObjectId(),
                **deepcopy(query),
                **update.get("$setOnInsert", {}),
            }
            self.documents.append(doc)
        doc.update(deepcopy(update["$set"]))


@pytest.fixture
def setup():
    db = {
        name: RetrievalCollection()
        for name in ("locations", "objects", "sources", "knowledge_chunks")
    }
    for location_id, slug in ((L1, "museum-one"), (L2, "museum-two")):
        db["locations"].documents.append(
            {
                **LOCATION,
                "_id": ObjectId(location_id),
                "slug": slug,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
    db["sources"].documents.append({"_id": ObjectId(S1), "location_id": L1})
    db["objects"].documents = [object_doc()]
    settings = Settings(mongodb_uri="mongodb://localhost", embedding_dimensions=2)
    repo = RetrievalRepository(db, settings)
    repo.index_status = AsyncMock(return_value="ready")
    repo.vector_search = AsyncMock(return_value=[])
    repo.embedding_count = AsyncMock(return_value=500)
    embedder = SimpleNamespace(
        model=settings.embedding_model,
        dimensions=2,
        embed=AsyncMock(return_value=EmbeddingBatch([[1.0, 0.0]], 12)),
    )
    return db, repo, embedder, RetrievalService(repo, embedder)


def test_normalization_and_accession_boundaries():
    assert normalize("  Henriëtte—RÖNNER\nKnip ") == "henriette ronner knip"
    obj = object_doc()
    for query in ("SK-C-5", "label SK C 5 Amsterdam", "skc5"):
        assert lexical_score(query, obj) == (1.0, ["identifier_exact"])
    for query in ("SK-C-50", "xSKC5", "SKC55"):
        assert "identifier_exact" not in lexical_score(query, obj)[1]
    assert lexical_score("De Nachtwacht\nRembrandt 1642", obj)[0] >= 0.86
    assert lexical_score("The Nlght Watch Rembrandt", obj)[0] >= 0.6


def test_generated_content_is_never_embedding_evidence():
    obj = object_doc()
    original = fingerprint(obj)
    obj["prepared_content"] = {"story": "An invented historical story"}
    obj["story"] = "another story"
    assert fingerprint(obj) == original
    assert "invented" not in search_text(obj)
    obj["description"] = "Changed source description"
    assert fingerprint(obj) != original


@pytest.mark.parametrize("text", ["", "   ", "---!!!", "x" * 2001])
def test_reject_invalid_query(text):
    with pytest.raises(ValidationError):
        SearchRequest(text=text)


def test_exact_match_skips_provider_and_is_museum_scoped(setup):
    db, repo, embedder, service = setup
    db["objects"].documents.append(object_doc(location_id=L2))
    result = asyncio.run(service.match(L1, SearchRequest(text="SK C 5")))
    assert result.status == "matched" and result.mode_used == "lexical_exact"
    assert result.matched_object_id == str(db["objects"].documents[0]["_id"])
    assert len(result.candidates) == 1
    embedder.embed.assert_not_awaited()
    repo.vector_search.assert_not_awaited()


def test_shared_title_remains_ambiguous_and_top_three(setup):
    db, _, embedder, service = setup
    db["objects"].documents.extend(object_doc(metadata={}) for _ in range(4))
    result = asyncio.run(service.match(L1, SearchRequest(text="The Night Watch")))
    assert result.status == "ambiguous" and result.matched_object_id is None
    assert len(result.candidates) == 3
    embedder.embed.assert_awaited_once()


def test_nonsense_does_not_force_a_match(setup):
    *_, service = setup
    result = asyncio.run(service.match(L1, SearchRequest(text="quantum penguin xyzzy")))
    assert result.status == "no_match" and result.candidates == []


def test_semantic_discovery_without_title_never_auto_matches(setup):
    db, repo, _, service = setup
    obj = db["objects"].documents[0]
    repo.vector_search.return_value = [
        {
            "object_id": str(obj["_id"]),
            "location_id": L1,
            "content_hash": fingerprint(obj),
            "score": 0.9,
        }
    ]
    result = asyncio.run(
        service.match(L1, SearchRequest(text="armed militia gathering"))
    )
    assert result.status == "ambiguous" and result.mode_used == "hybrid"
    assert result.candidates[0].vector_score == 0.9
    assert "semantic" in result.candidates[0].signals


@pytest.mark.parametrize("corruption", ["location", "hash", "deleted"])
def test_stale_and_cross_museum_vectors_are_discarded(setup, corruption):
    db, repo, _, service = setup
    obj = db["objects"].documents[0]
    row = {
        "object_id": str(obj["_id"]),
        "location_id": L1,
        "content_hash": fingerprint(obj),
        "score": 0.99,
    }
    row[
        {"location": "location_id", "hash": "content_hash", "deleted": "object_id"}[
            corruption
        ]
    ] = "invalid"
    repo.vector_search.return_value = [row]
    result = asyncio.run(
        service.search(L1, SearchRequest(text="militia", mode="semantic"))
    )
    assert not result.candidates
    assert "STALE_VECTOR_RESULTS_IGNORED" in result.warnings


@pytest.mark.parametrize(
    "failure",
    [RetrievalFailure("EMBEDDING_QUOTA_EXHAUSTED"), OperationFailure("secret host")],
)
def test_hybrid_falls_back_explicitly_semantic_fails_safely(setup, failure):
    _, repo, _, service = setup
    repo.index_status.side_effect = failure
    result = asyncio.run(service.search(L1, SearchRequest(text="Rembrandt")))
    assert result.mode_used == "lexical" and result.warnings
    assert "secret" not in result.model_dump_json()
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            service.search(L1, SearchRequest(text="Rembrandt", mode="semantic"))
        )
    assert error.value.status_code == 503 and "secret" not in error.value.message


def test_missing_index_does_not_spend_on_embedding(setup):
    _, repo, embedder, service = setup
    repo.index_status.return_value = "building"
    result = asyncio.run(service.search(L1, SearchRequest(text="militia")))
    assert result.warnings == ["VECTOR_INDEX_NOT_READY"]
    embedder.embed.assert_not_awaited()


def test_museum_must_exist_and_ids_validate(setup):
    *_, service = setup
    for location, status in (("invalid", 422), (str(ObjectId()), 404)):
        with pytest.raises(ApplicationError) as error:
            asyncio.run(service.search(location, SearchRequest(text="Night Watch")))
        assert error.value.status_code == status


def test_corpus_limit_is_explicit_not_silent_truncation(setup):
    db, _, _, service = setup
    db["objects"].documents = [object_doc() for _ in range(501)]
    with pytest.raises(ApplicationError) as error:
        asyncio.run(service.search(L1, SearchRequest(text="Night Watch")))
    assert error.value.code == "MUSEUM_EXCEEDS_MVP_LIMIT"


def test_preparation_idempotence_regeneration_and_provenance(setup):
    db, repo, embedder, _ = setup
    original_objects = deepcopy(db["objects"].documents)
    original_sources = deepcopy(db["sources"].documents)

    async def run():
        assert (await prepare(repo, L1, embedder, 3))["generated"] == 1
        assert (await prepare(repo, L1, embedder, 3))["skipped"] == 1
        assert embedder.embed.await_count == 1
        assert db["objects"].documents == original_objects
        assert db["sources"].documents == original_sources
        db["objects"].documents[0]["year"] = 1643
        assert (await prepare(repo, L1, embedder, 3))["generated"] == 1

    asyncio.run(run())
    chunks = db["knowledge_chunks"].documents
    assert len(chunks) == 1
    assert chunks[0]["source_ids"] == [S1] and chunks[0]["origin"] == "canonical_object"
    assert "1643" in chunks[0]["text"]


def test_provider_failure_preserves_previous_embedding(setup):
    db, repo, embedder, _ = setup

    async def run():
        await prepare(repo, L1, embedder, 3)
        original = deepcopy(db["knowledge_chunks"].documents)
        db["objects"].documents[0]["year"] = 1643
        embedder.embed.side_effect = RetrievalFailure("EMBEDDING_QUOTA_EXHAUSTED")
        result = await prepare(repo, L1, embedder, 3)
        assert result["failed"] == 1 and result["aborted"]
        assert db["knowledge_chunks"].documents == original

    asyncio.run(run())


def test_concurrent_source_change_rejects_embedding(setup):
    db, repo, embedder, _ = setup

    async def change(texts):
        db["objects"].documents[0]["name"] = "Updated identity"
        return EmbeddingBatch([[1.0, 0.0]], 5)

    embedder.embed.side_effect = change
    result = asyncio.run(prepare(repo, L1, embedder, 3))
    assert result["errors"][0]["code"] == "EVIDENCE_CHANGED"
    assert not db["knowledge_chunks"].documents


def test_missing_or_wrong_museum_source_is_not_embedded(setup):
    db, repo, embedder, _ = setup
    db["sources"].documents[0]["location_id"] = L2
    result = asyncio.run(prepare(repo, L1, embedder, 3))
    assert result["errors"][0]["code"] == "MISSING_SOURCE_EVIDENCE"
    embedder.embed.assert_not_awaited()


@pytest.mark.parametrize(
    "vector", [[0, 0], [1], [True, 1], [float("nan"), 1], [float("inf"), 1]]
)
def test_reject_invalid_vectors(vector):
    with pytest.raises(RetrievalFailure):
        validate_vector(vector, 2)


def test_embedding_provider_reorders_batch_and_validates_model():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["dimensions"] == 2 and payload["input"] == ["one", "two"]
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "usage": {"total_tokens": 4},
                "data": [
                    {"index": 1, "embedding": [0, 1]},
                    {"index": 0, "embedding": [1, 0]},
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await OpenAIEmbedder(http, "test-model", 2).embed(["one", "two"])
            assert result.vectors == [[1, 0], [0, 1]] and result.input_tokens == 4

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {"secret": "secret key"}, "EMBEDDING_ACCESS_DENIED"),
        (
            429,
            {"error": {"code": "credit_balance_exhausted"}},
            "EMBEDDING_QUOTA_EXHAUSTED",
        ),
        (200, {"data": []}, "INVALID_EMBEDDING"),
        (500, {"error": "secret host"}, "EMBEDDING_REQUEST_FAILED"),
    ],
)
def test_provider_failures_are_safe(status, body, code):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=body)
            )
        ) as http:
            with pytest.raises(RetrievalFailure, match=f"^{code}$"):
                await OpenAIEmbedder(http, "test", 2).embed(["query"])

    asyncio.run(run())


def test_atlas_pipeline_prefilters_museum_and_model(setup):
    db, repo, _, _ = setup
    repo.vector_search = RetrievalRepository.vector_search.__get__(repo)
    db["knowledge_chunks"].aggregate = AsyncMock(return_value=RetrievalCursor([]))
    asyncio.run(repo.vector_search(L1, [1, 0]))
    pipeline = db["knowledge_chunks"].aggregate.call_args.args[0]
    query = pipeline[0]["$vectorSearch"]
    assert query["filter"]["location_id"] == L1
    assert query["filter"]["embedding_model"] == repo.settings.embedding_model
    assert query["filter"]["embedding_dimensions"] == 2
    assert query["numCandidates"] >= query["limit"]


def test_index_lifecycle_and_configuration_mismatch(setup):
    db, repo, _, _ = setup
    repo.index_status = RetrievalRepository.index_status.__get__(repo)
    collection = db["knowledge_chunks"]
    collection.list_search_indexes = AsyncMock(return_value=RetrievalCursor([]))
    collection.create_search_index = AsyncMock()

    async def run():
        assert await repo.index_status() == "missing"
        assert await repo.index_status(create=True) == "building"
        collection.create_search_index.assert_awaited_once()
        index = {
            "type": "vectorSearch",
            "queryable": True,
            "status": "READY",
            "latestDefinition": index_definition(2),
        }
        collection.list_search_indexes.return_value = RetrievalCursor([index])
        assert await repo.index_status(create=True) == "ready"
        collection.create_search_index.assert_awaited_once()
        index["latestDefinition"] = index_definition(3)
        with pytest.raises(RetrievalFailure, match="CONFIGURATION_MISMATCH"):
            await repo.index_status(create=True)

    asyncio.run(run())


def test_routes_validation_and_match_contract(setup, monkeypatch):
    db, _, _, _ = setup
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost")
    with patch("app.main.MongoDB") as factory:
        database = factory.return_value
        database.ping = AsyncMock()
        database.ensure_indexes = AsyncMock()
        database.close = AsyncMock()
        database.database = db
        with TestClient(create_app()) as client:
            result = client.post(
                "/api/v1/recognition/match",
                json={"location_id": L1, "text": "SK-C-5", "mode": "lexical"},
            )
            assert result.status_code == 200
            assert result.json()["status"] == "matched"
            assert "embedding" not in result.text
            for data in (
                {"text": "query"},
                {"location_id": L1, "text": "---"},
                {"location_id": L1, "text": "query", "limit": 5},
            ):
                assert (
                    client.post("/api/v1/recognition/match", json=data).status_code
                    == 422
                )
            assert (
                client.post(
                    f"/api/v1/locations/{L1}/search",
                    json={"text": "De Nachtwacht", "mode": "lexical", "limit": 1},
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/v1/locations/invalid/search",
                    json={"text": "query", "mode": "lexical"},
                ).status_code
                == 422
            )


def test_match_request_requires_museum():
    with pytest.raises(ValidationError):
        MatchRequest(text="query")


def test_no_embeddings_does_not_spend_on_query(setup):
    _, repo, embedder, service = setup
    repo.embedding_count.return_value = 0
    result = asyncio.run(service.search(L1, SearchRequest(text="militia")))
    assert result.warnings == ["TEXT_EMBEDDINGS_NOT_PREPARED"]
    embedder.embed.assert_not_awaited()


def test_partial_coverage_is_reported(setup):
    db, repo, _, service = setup
    db["objects"].documents.append(object_doc(name="A different painting", aliases=[]))
    repo.embedding_count.return_value = 1
    result = asyncio.run(service.search(L1, SearchRequest(text="militia")))
    assert "PARTIAL_EMBEDDING_COVERAGE" in result.warnings


def test_retry_transient_provider_failure():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "model": "test",
                "usage": {"total_tokens": 1},
                "data": [{"index": 0, "embedding": [1, 0]}],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with patch("app.modules.retrieval.provider.asyncio.sleep", new=AsyncMock()):
                result = await OpenAIEmbedder(http, "test", 2, attempts=3).embed(
                    ["text"]
                )
                assert result.vectors == [[1, 0]]

    asyncio.run(run())
    assert len(calls) == 2


def test_model_or_dimensions_change_requires_reembedding(setup):
    db, repo, embedder, _ = setup

    async def run():
        await prepare(repo, L1, embedder, 3)
        embedder.model = "another-model"
        assert (await prepare(repo, L1, embedder, 3))["generated"] == 1
        embedder.dimensions = 3
        embedder.embed.return_value = EmbeddingBatch([[1, 0, 0]], 10)
        assert (await prepare(repo, L1, embedder, 3))["generated"] == 1

    asyncio.run(run())
    assert len(db["knowledge_chunks"].documents) == 3


def test_common_words_and_short_first_name_do_not_create_candidates():
    obj = object_doc(
        name="Portrait of a woman", aliases=[], creator="Jan Steen", metadata={}
    )
    assert lexical_score("six monkeys in a cage", obj)[0] == 0
    assert lexical_score("Misty Sea Jan Toorop 1899", obj)[0] == 0
    assert "creator" in lexical_score("Jan Steen", obj)[1]


@pytest.mark.parametrize("value", ["free text", ["SK-C-5"], 123, None])
def test_arbitrary_manual_metadata_does_not_break_search(value):
    obj = object_doc(metadata={"rijksmuseum": value})
    assert lexical_score("The Night Watch", obj)[0] >= 0.86
    assert search_text(obj)
