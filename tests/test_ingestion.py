import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from bson import ObjectId

from app.core.exceptions import ApplicationError
from app.modules.ingestion.repository import ImportRepository
from app.modules.ingestion.rijksmuseum import (
    ENGLISH,
    PRIMARY_TITLE,
    SEARCH_URL,
    ImportFailure,
    RijksmuseumClient,
    map_record,
)
from app.modules.ingestion.service import import_rijksmuseum
from app.modules.objects.models import Object
from app.modules.sources.models import Source
from tests.test_domain import Collection

IDENTIFIER = "https://id.rijksmuseum.nl/200100988"


def record(identifier=IDENTIFIER, title="A museum title"):
    return {
        "id": identifier,
        "type": "HumanMadeObject",
        "identified_by": [
            {
                "type": "Name",
                "content": title,
                "classified_as": [{"id": PRIMARY_TITLE}],
                "language": [{"id": ENGLISH}],
            },
            {"type": "Name", "content": "Een titel"},
        ],
        "produced_by": {
            "timespan": {
                "identified_by": [{"type": "Name", "content": "circa 1642–1645"}]
            },
            "part": [
                {
                    "carried_out_by": [
                        {
                            "notation": [
                                {"@language": "nl", "@value": "Nederlandse naam"},
                                {"@language": "en", "@value": "English name"},
                            ]
                        }
                    ]
                }
            ],
        },
        "classified_as": [{"notation": {"@language": "en", "@value": "painting"}}],
    }


def test_mapping_preserves_evidence_and_uncertain_dates():
    raw = record()
    mapped = map_record(IDENTIFIER, raw)
    assert mapped.object.name == "A museum title"
    assert mapped.object.aliases == ["Een titel"]
    assert mapped.object.creator == "English name"
    assert mapped.object.year == "circa 1642–1645"
    assert mapped.object.facts == []
    assert mapped.object.story is None
    assert mapped.raw_record == raw


@pytest.mark.parametrize(
    "change,code",
    [
        ({"id": "wrong"}, "INVALID_RECORD_ID_OR_TYPE"),
        ({"type": "Person"}, "INVALID_RECORD_ID_OR_TYPE"),
        ({"identified_by": []}, "MISSING_TITLE"),
        ({"produced_by": []}, "INVALID_RECORD_STRUCTURE"),
    ],
)
def test_invalid_records(change, code):
    with pytest.raises(ImportFailure, match=code):
        map_record(IDENTIFIER, {**record(), **change})


def test_pagination_deduplicates_and_limits_requests():
    seen = []
    second = "https://id.rijksmuseum.nl/200104600"

    def handler(request):
        seen.append(str(request.url))
        if request.url.params.get("pageToken"):
            return httpx.Response(
                200, json={"orderedItems": [{"id": IDENTIFIER}, {"id": second}]}
            )
        return httpx.Response(
            200,
            json={
                "orderedItems": [{"id": IDENTIFIER}],
                "next": {"id": SEARCH_URL + "?pageToken=next"},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = RijksmuseumClient(http)
            assert [item async for item in client.identifiers(2)] == [
                IDENTIFIER,
                second,
            ]

    asyncio.run(run())
    assert len(seen) == 2


def test_network_retry_and_safe_http_error():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="private remote details")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ImportFailure, match="^PROVIDER_HTTP_ERROR$"):
                await RijksmuseumClient(http).get_json(SEARCH_URL)

    with patch(
        "app.modules.ingestion.rijksmuseum.asyncio.sleep", new_callable=AsyncMock
    ):
        asyncio.run(run())
    assert attempts == 3


@pytest.mark.parametrize(
    "payload,code",
    [
        ({}, "INVALID_PROVIDER_PAGE"),
        ({"orderedItems": [{"id": "http://localhost/private"}]}, "INVALID_PROVIDER_ID"),
        (
            {"orderedItems": [], "next": {"id": SEARCH_URL + "?x=1"}},
            "EMPTY_PROVIDER_PAGE",
        ),
        (
            {
                "orderedItems": [{"id": IDENTIFIER}],
                "next": {"id": SEARCH_URL + "?type=painting"},
            },
            "REPEATED_PROVIDER_PAGE",
        ),
    ],
)
def test_invalid_or_looping_pages(payload, code):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
        ) as http:
            with pytest.raises(ImportFailure, match=code):
                [item async for item in RijksmuseumClient(http).identifiers(100)]

    asyncio.run(run())


def test_untrusted_url_is_not_requested():
    async def run():
        http = AsyncMock()
        with pytest.raises(ImportFailure, match="UNEXPECTED_PROVIDER_URL"):
            await RijksmuseumClient(http).get_json("https://evil.example/data")
        http.get.assert_not_called()

    asyncio.run(run())


class ImportCollection(Collection):
    """Exercise MongoDB update operators without any real credentials."""

    async def update_one(self, query, update, upsert=False):
        existing = next(
            (doc for doc in self.documents if self.matches(doc, query)), None
        )
        inserting = existing is None
        if inserting:
            assert upsert
            existing = {**query, "_id": ObjectId()}
            self.documents.append(existing)
        for operator, fields in update.items():
            if operator == "$setOnInsert" and not inserting:
                continue
            for path, value in fields.items():
                keys = path.split(".")
                target = existing
                for key in keys[:-1]:
                    target = target.setdefault(key, {})
                if operator == "$addToSet":
                    values = (
                        value.get("$each", []) if isinstance(value, dict) else [value]
                    )
                    target.setdefault(keys[-1], [])
                    for item in values:
                        if item not in target[keys[-1]]:
                            target[keys[-1]].append(deepcopy(item))
                else:
                    target[keys[-1]] = deepcopy(value)
        return SimpleNamespace(upserted_id=existing["_id"] if inserting else None)


def test_repeat_import_update_and_preservation():
    async def run():
        db = {"objects": ImportCollection(), "sources": ImportCollection()}
        repo = ImportRepository(db)
        original = record()
        first = map_record(IDENTIFIER, original)
        assert await repo.save("location1", first) == "imported"
        obj = db["objects"].documents[0]
        identity, created = obj["_id"], obj["created_at"]
        obj.update(
            facts=["Curated fact"],
            story="Curated story",
            description="Prior description",
        )
        obj.setdefault("metadata", {})["manual"] = "Keep me"
        obj["source_ids"].append("manual-source")
        assert await repo.save("location1", first) == "skipped"
        changed = record(title="Updated title")
        changed.pop("produced_by")
        changed.pop("classified_as")
        assert (
            await repo.save("location1", map_record(IDENTIFIER, changed)) == "updated"
        )
        assert len(db["objects"].documents) == len(db["sources"].documents) == 1
        obj = db["objects"].documents[0]
        assert obj["_id"] == identity and obj["created_at"] == created
        assert obj["name"] == "Updated title"
        assert obj["type"] == "painting"
        assert obj["creator"] == "English name"  # Empty incoming data does not erase.
        assert obj["facts"] == ["Curated fact"] and obj["story"] == "Curated story"
        assert obj["description"] == "Prior description"
        assert obj["metadata"]["manual"] == "Keep me"
        assert "manual-source" in obj["source_ids"]
        assert db["sources"].documents[0]["raw_record"] == changed
        # Existing API response models accept the persisted documents.
        Object.model_validate(
            {"id": str(obj["_id"]), **{k: v for k, v in obj.items() if k != "_id"}}
        )
        source = db["sources"].documents[0]
        Source.model_validate(
            {
                "id": str(source["_id"]),
                **{k: v for k, v in source.items() if k != "_id"},
            }
        )
        assert await repo.save("location2", first) == "imported"
        assert len(db["objects"].documents) == 2

    asyncio.run(run())


def test_partial_failure_report_continues():
    async def identifiers(limit):
        yield IDENTIFIER
        yield "https://id.rijksmuseum.nl/200104600"

    async def run():
        provider = SimpleNamespace(
            identifiers=identifiers,
            record=AsyncMock(
                side_effect=[
                    ImportFailure("MISSING_TITLE"),
                    map_record(IDENTIFIER, record()),
                ]
            ),
        )
        locations = AsyncMock()
        locations.get.return_value = SimpleNamespace(id="location1", slug="rijksmuseum")
        repo = AsyncMock()
        repo.save.return_value = "imported"
        report = await import_rijksmuseum("location1", 2, locations, provider, repo)
        assert report.attempted == 2 and report.imported == 1 and report.failed == 1
        assert report.errors == [{"external_id": IDENTIFIER, "code": "MISSING_TITLE"}]

    asyncio.run(run())


def test_wrong_destination_rejected_before_fetch():
    async def run():
        locations = AsyncMock()
        locations.get.return_value = SimpleNamespace(id="location1", slug="louvre")
        provider = AsyncMock()
        with pytest.raises(ApplicationError) as error:
            await import_rijksmuseum("location1", 100, locations, provider, AsyncMock())
        assert error.value.code == "IMPORT_LOCATION_MISMATCH"
        provider.identifiers.assert_not_called()

    asyncio.run(run())


def test_multiple_date_ranges_and_description_language():
    raw = record()
    raw["produced_by"]["timespan"] = [
        {"identified_by": [{"content": "1525–1574"}]},
        {"identified_by": [{"content": "1585–1650"}]},
    ]
    raw["subject_of"] = [
        {
            "language": [{"id": ENGLISH}],
            "part": [
                {
                    "content": "Museum-supplied description.",
                    "classified_as": [{"id": "http://vocab.getty.edu/aat/300048722"}],
                }
            ],
        }
    ]
    mapped = map_record(IDENTIFIER, raw)
    assert mapped.object.year == "1525–1574; 1585–1650"
    assert mapped.object.description == "Museum-supplied description."


def test_page_failure_report_keeps_completed_counts():
    async def identifiers(limit):
        yield IDENTIFIER
        raise ImportFailure("PROVIDER_NETWORK_ERROR")

    async def run():
        locations = AsyncMock()
        locations.get.return_value = SimpleNamespace(id="location1", slug="rijksmuseum")
        provider = SimpleNamespace(
            identifiers=identifiers,
            record=AsyncMock(return_value=map_record(IDENTIFIER, record())),
        )
        repo = AsyncMock()
        repo.save.return_value = "skipped"
        report = await import_rijksmuseum("location1", 100, locations, provider, repo)
        assert report.attempted == report.skipped == 1
        assert report.errors == [{"code": "PROVIDER_NETWORK_ERROR"}]

    asyncio.run(run())


def test_cli_database_failure_is_sanitized_and_client_closed(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    from pymongo.errors import ConnectionFailure

    from app.modules.ingestion.__main__ import run

    with patch("app.modules.ingestion.__main__.MongoDB") as factory:
        db = factory.return_value
        db.ping = AsyncMock(side_effect=ConnectionFailure("private connection details"))
        db.close = AsyncMock()
        with patch("builtins.print") as output:
            assert asyncio.run(run("location1", 100)) == 1
        assert "DATABASE_ERROR" in output.call_args.args[0]
        assert "private" not in output.call_args.args[0]
        db.close.assert_awaited_once()


@pytest.mark.parametrize("value", ["0", "501", "-1"])
def test_cli_limit_validation(value):
    import argparse

    from app.modules.ingestion.__main__ import import_limit

    with pytest.raises(argparse.ArgumentTypeError):
        import_limit(value)
