import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bson import ObjectId

from app.core.exceptions import ApplicationError
from app.modules.knowledge.models import VisitorContent
from app.modules.knowledge.provider import (
    GenerationFailure,
    GenerationResult,
    OpenAIContentGenerator,
)
from app.modules.knowledge.service import KnowledgeService, validate_content
from tests.test_domain import Collection

SOURCE_ID = str(ObjectId())
OBJECT_ID = str(ObjectId())
RAW = {"description": "A painting made in 1899.", "name": "Misty Sea"}


def content(quote="A painting made in 1899.", source_id=SOURCE_ID):
    return VisitorContent.model_validate(
        {
            "introduction": {
                "text": "This painting dates from 1899.",
                "citations": [{"source_id": source_id, "quote": quote}],
            },
            "facts": [],
            "story": None,
            "history": None,
            "interesting_fact": None,
        }
    )


class KnowledgeCollection(Collection):
    def matches(self, document, query):
        return all(document.get(key) == value for key, value in query.items())

    async def update_one(self, query, update):
        doc = next((doc for doc in self.documents if self.matches(doc, query)), None)
        if doc is None:
            return SimpleNamespace(matched_count=0)
        doc.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=1)


@pytest.fixture
def setup():
    db = {"objects": KnowledgeCollection(), "sources": KnowledgeCollection()}
    db["objects"].documents.append(
        {
            "_id": ObjectId(OBJECT_ID),
            "location_id": "location",
            "name": "Misty Sea",
            "source_ids": [SOURCE_ID],
            "facts": ["Canonical fact"],
        }
    )
    db["sources"].documents.append(
        {
            "_id": ObjectId(SOURCE_ID),
            "location_id": "location",
            "raw_record": deepcopy(RAW),
        }
    )
    generator = SimpleNamespace(
        provider="test",
        model="test-model",
        generate=AsyncMock(
            return_value=GenerationResult(content(), "test-model", 10, 20)
        ),
    )
    return db, generator, KnowledgeService(db)


def test_generation_and_repeat_preserve_sources_and_canonical_content(setup):
    db, generator, service = setup
    original_sources = deepcopy(db["sources"].documents)

    async def run():
        assert await service.prepare(OBJECT_ID, generator) == "generated"
        assert await service.prepare(OBJECT_ID, generator) == "skipped"
        assert generator.generate.await_count == 1
        saved = await service.experience(OBJECT_ID)
        assert saved.source_ids == [SOURCE_ID]
        assert saved.review_status == "unreviewed"
        assert saved.input_tokens == 10 and saved.output_tokens == 20
        assert await service.prepare(OBJECT_ID, generator, force=True) == "generated"

    asyncio.run(run())
    assert db["sources"].documents == original_sources
    assert db["objects"].documents[0]["facts"] == ["Canonical fact"]
    evidence = json.loads(generator.generate.call_args.args[0])
    assert evidence == {SOURCE_ID: RAW}


@pytest.mark.parametrize(
    "quote,source_id", [("Invented evidence", SOURCE_ID), ("1899", str(ObjectId()))]
)
def test_invalid_citations(quote, source_id):
    with pytest.raises(GenerationFailure, match="INVALID_EVIDENCE_CITATION"):
        validate_content(content(quote, source_id), {SOURCE_ID: RAW})


def test_failed_regeneration_does_not_replace_good_content(setup):
    db, generator, service = setup

    async def run():
        await service.prepare(OBJECT_ID, generator)
        previous = deepcopy(db["objects"].documents[0]["prepared_content"])
        generator.generate.side_effect = GenerationFailure("PROVIDER_REFUSAL")
        with pytest.raises(GenerationFailure):
            await service.prepare(OBJECT_ID, generator, force=True)
        assert db["objects"].documents[0]["prepared_content"] == previous

    asyncio.run(run())


def test_changed_evidence_regenerates(setup):
    db, generator, service = setup

    async def run():
        await service.prepare(OBJECT_ID, generator)
        db["sources"].documents[0]["raw_record"]["additional"] = "More source context."
        assert await service.prepare(OBJECT_ID, generator) == "generated"
        assert generator.generate.await_count == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,code",
    [("empty", "MISSING_SOURCE_EVIDENCE"), ("foreign", "INVALID_SOURCE_REFERENCE")],
)
def test_missing_and_foreign_evidence_never_calls_provider(setup, kind, code):
    db, generator, service = setup
    if kind == "empty":
        db["sources"].documents[0]["raw_record"] = None
    else:
        db["sources"].documents[0]["location_id"] = "other"
    with pytest.raises(GenerationFailure, match=code):
        asyncio.run(service.prepare(OBJECT_ID, generator))
    generator.generate.assert_not_called()


def test_concurrent_import_rejects_stale_generation(setup):
    db, generator, service = setup

    async def generate(evidence):
        db["sources"].documents[0]["raw_record"]["name"] = "Changed"
        return GenerationResult(content(), "test-model")

    generator.generate.side_effect = generate
    with pytest.raises(GenerationFailure, match="EVIDENCE_CHANGED"):
        asyncio.run(service.prepare(OBJECT_ID, generator))
    assert "prepared_content" not in db["objects"].documents[0]


def test_empty_output_is_not_saved(setup):
    db, generator, service = setup
    value = content().model_copy(update={"introduction": None})
    generator.generate.return_value = GenerationResult(value, "test-model")
    with pytest.raises(GenerationFailure, match="INSUFFICIENT_EVIDENCE"):
        asyncio.run(service.prepare(OBJECT_ID, generator))
    assert "prepared_content" not in db["objects"].documents[0]


def test_experience_not_prepared(setup):
    _, _, service = setup
    with pytest.raises(ApplicationError) as error:
        asyncio.run(service.experience(OBJECT_ID))
    assert error.value.code == "CONTENT_NOT_PREPARED"


def response_data():
    return {
        "status": "completed",
        "model": "test-model",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(
                            {
                                "introduction": {
                                    "text": "This painting dates from 1899.",
                                    "evidence_ids": [0],
                                },
                                "facts": [],
                                "story": None,
                                "history": None,
                                "interesting_fact": None,
                            }
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def test_provider_structured_contract_and_usage():
    def handler(request):
        sent = json.loads(request.content)
        assert sent["store"] is False
        assert sent["text"]["format"]["strict"] is True
        assert json.loads(sent["input"])[0]["quote"] == RAW["description"]
        return httpx.Response(200, json=response_data())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await OpenAIContentGenerator(http, "test-model").generate(
                json.dumps({SOURCE_ID: RAW})
            )
            assert result.input_tokens == 10 and result.output_tokens == 20

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,payload,code",
    [
        (401, {}, "PROVIDER_ACCESS_DENIED"),
        (
            429,
            {
                "error": {
                    "type": "insufficient_quota",
                    "code": "credit_balance_exhausted",
                }
            },
            "PROVIDER_QUOTA_EXHAUSTED",
        ),
        (429, {"error": {"code": "insufficient_quota"}}, "PROVIDER_QUOTA_EXHAUSTED"),
        (200, {"status": "incomplete"}, "INCOMPLETE_GENERATION"),
        (
            200,
            {
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            },
            "PROVIDER_REFUSAL",
        ),
        (200, {"status": "completed", "output": []}, "INVALID_GENERATION"),
    ],
)
def test_provider_failure_codes(status, payload, code):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status, json=payload)
            )
        ) as http:
            with pytest.raises(GenerationFailure, match=code):
                await OpenAIContentGenerator(http, "test").generate(
                    json.dumps({SOURCE_ID: RAW})
                )

    asyncio.run(run())


def test_provider_transient_retry():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 3 else 200, json=response_data())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await OpenAIContentGenerator(http, "test").generate(
                json.dumps({SOURCE_ID: RAW})
            )

    with patch("app.modules.knowledge.provider.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(run())
    assert calls == 3


def test_experience_endpoint_and_schema(setup, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    db, generator, service = setup
    asyncio.run(service.prepare(OBJECT_ID, generator))
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    with patch("app.main.MongoDB") as factory:
        mongodb = factory.return_value
        mongodb.ping = AsyncMock()
        mongodb.ensure_indexes = AsyncMock()
        mongodb.close = AsyncMock()
        mongodb.database = db
        with TestClient(create_app()) as client:
            response = client.get(f"/api/v1/objects/{OBJECT_ID}/experience")
            assert response.status_code == 200
            assert response.json()["provider"] == "test"
            assert response.json()["review_status"] == "unreviewed"
            assert client.get("/api/v1/objects/invalid/experience").status_code == 422
            assert (
                client.get(f"/api/v1/objects/{ObjectId()}/experience").status_code
                == 404
            )
            assert (
                "/api/v1/objects/{object_id}/experience"
                in client.get("/openapi.json").json()["paths"]
            )


def test_cli_missing_key_and_quota_abort(monkeypatch, capsys):
    from app.core.config import Settings
    from app.modules.knowledge.__main__ import run

    settings = Settings(mongodb_uri="mongodb://localhost:27017", openai_api_key=None)
    with patch("app.modules.knowledge.__main__.Settings", return_value=settings):
        assert asyncio.run(run(str(ObjectId()), 3, 0, False)) == 1
    assert "OPENAI_API_KEY_REQUIRED" in capsys.readouterr().out


def test_numbered_evidence_preserves_exact_quotes():
    from app.modules.knowledge.evidence import (
        ReferencedContent,
        evidence_catalog,
        resolve_content,
    )

    raw = {"name": "Misty Sea", "description": "Exact text… including punctuation."}
    catalog = evidence_catalog(json.dumps({SOURCE_ID: raw}))
    response = ReferencedContent.model_validate(
        {
            "introduction": {"text": "A visitor introduction", "evidence_ids": [1]},
            "facts": [],
            "story": None,
            "history": None,
            "interesting_fact": None,
        }
    )
    resolved = resolve_content(response, catalog)
    assert resolved.introduction.citations[0].quote == raw["description"]
    validate_content(resolved, {SOURCE_ID: raw})
    response.introduction.evidence_ids = [999]
    with pytest.raises(ValueError, match="Unknown evidence ID"):
        resolve_content(response, catalog)
