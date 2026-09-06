"""API/service/repository tests with a small isolated collection double."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.main import create_app


class Cursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        self.documents.sort(key=lambda doc: doc[key])
        return self

    def skip(self, offset):
        self.documents = self.documents[offset:]
        return self

    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self

    async def __aiter__(self):
        for document in self.documents:
            yield deepcopy(document)


class Collection:
    def __init__(self):
        self.documents = []

    def matches(self, document, query):
        return all(
            document.get(key) in value["$in"]
            if isinstance(value, dict)
            else document.get(key) == value
            for key, value in query.items()
        )

    async def insert_one(self, document):
        if "slug" in document and any(
            doc["slug"] == document["slug"] for doc in self.documents
        ):
            raise DuplicateKeyError("private database details")
        document["_id"] = ObjectId()
        self.documents.append(deepcopy(document))
        return type("InsertResult", (), {"inserted_id": document["_id"]})()

    async def find_one(self, query):
        return next(
            (deepcopy(doc) for doc in self.documents if self.matches(doc, query)), None
        )

    def find(self, query):
        return Cursor([doc for doc in self.documents if self.matches(doc, query)])

    async def count_documents(self, query):
        return sum(self.matches(doc, query) for doc in self.documents)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    with patch("app.main.MongoDB") as factory:
        db = factory.return_value
        db.ping = AsyncMock()
        db.ensure_indexes = AsyncMock()
        db.close = AsyncMock()
        db.database = {
            name: Collection() for name in ("locations", "objects", "sources")
        }
        with TestClient(create_app()) as client:
            yield client


LOCATION = {
    "name": "Rijksmuseum",
    "slug": "rijksmuseum",
    "type": "museum",
    "city": "Amsterdam",
    "country": "Netherlands",
    "official_website": "https://www.rijksmuseum.nl/",
}
SOURCE = {
    "title": "Museum collection",
    "type": "official_website",
    "url": "https://www.rijksmuseum.nl/en/collection",
}
OBJECT = {"name": "The Night Watch", "type": "painting"}


def create_location(client, slug="rijksmuseum"):
    response = client.post("/api/v1/locations", json={**LOCATION, "slug": slug})
    assert response.status_code == 201
    return response.json()


def test_location_flow_and_duplicate(client):
    location = create_location(client, " Rijksmuseum ")
    assert location["slug"] == "rijksmuseum"
    assert location["created_at"] == location["updated_at"]
    assert "_id" not in location
    assert client.get(f"/api/v1/locations/{location['id']}").json() == location
    assert client.get("/api/v1/locations").json() == [location]
    response = client.post("/api/v1/locations", json=LOCATION)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LOCATION_SLUG_ALREADY_EXISTS"
    create_location(client, "another")
    assert len(client.get("/api/v1/locations?limit=1&offset=1").json()) == 1
    assert client.get("/api/v1/locations?offset=2").json() == []


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"slug": " "},
        {"slug": "bad slug"},
        {"city": ""},
        {"country": " "},
        {"type": "unknown"},
        {"status": "ready"},
        {"coordinates": {"latitude": 91, "longitude": 0}},
        {"coordinates": {"latitude": 0, "longitude": -181}},
        {"official_website": "not-a-url"},
        {"id": "client-supplied"},
    ],
)
def test_location_validation(client, change):
    response = client.post("/api/v1/locations", json={**LOCATION, **change})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_source_and_object_flow(client):
    location = create_location(client)
    base = f"/api/v1/locations/{location['id']}"
    source_response = client.post(f"{base}/sources", json=SOURCE)
    assert source_response.status_code == 201
    source = source_response.json()
    assert client.get(f"/api/v1/sources/{source['id']}").json() == source
    assert client.get(f"{base}/sources").json() == [source]
    response = client.post(
        f"{base}/objects",
        json={
            **OBJECT,
            "source_ids": [source["id"], source["id"]],
            "metadata": {"medium": "oil", "dimensions": {"width": 100}},
            "year": "c. 1642",
        },
    )
    assert response.status_code == 201
    obj = response.json()
    assert obj["source_ids"] == [source["id"]]
    assert obj["location_id"] == location["id"]
    assert obj["facts"] == [] and obj["story"] is None and obj["history"] is None
    assert client.get(f"/api/v1/objects/{obj['id']}").json() == obj
    assert client.get(f"{base}/objects").json() == [obj]
    other = create_location(client, "other")
    for collection in ("objects", "sources"):
        assert client.get(f"/api/v1/locations/{other['id']}/{collection}").json() == []
        assert client.get(f"{base}/{collection}?offset=1").json() == []


@pytest.mark.parametrize(
    "collection,code",
    [
        ("locations", "LOCATION_NOT_FOUND"),
        ("objects", "OBJECT_NOT_FOUND"),
        ("sources", "SOURCE_NOT_FOUND"),
    ],
)
def test_missing_and_malformed_ids(client, collection, code):
    response = client.get(f"/api/v1/{collection}/{ObjectId()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == code
    response = client.get(f"/api/v1/{collection}/invalid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_OBJECT_ID"


@pytest.mark.parametrize(
    "collection,payload", [("objects", OBJECT), ("sources", SOURCE)]
)
@pytest.mark.parametrize("location_id,status", [(str(ObjectId()), 404), ("bad", 422)])
def test_child_requires_location(client, collection, payload, location_id, status):
    path = f"/api/v1/locations/{location_id}/{collection}"
    assert client.post(path, json=payload).status_code == status
    assert client.get(path).status_code == status


@pytest.mark.parametrize("kind", ["malformed", "missing", "different_location"])
def test_source_relationship_validation(client, kind):
    location = create_location(client)
    source_id = "invalid" if kind == "malformed" else str(ObjectId())
    if kind == "different_location":
        other = create_location(client, "other")
        source_id = client.post(
            f"/api/v1/locations/{other['id']}/sources", json=SOURCE
        ).json()["id"]
    response = client.post(
        f"/api/v1/locations/{location['id']}/objects",
        json={
            **OBJECT,
            "source_ids": [source_id],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SOURCE_REFERENCE"


@pytest.mark.parametrize(
    "collection,payload",
    [
        ("objects", {"type": "painting"}),
        ("objects", {**OBJECT, "name": " "}),
        ("sources", {**SOURCE, "url": "file:///secret"}),
    ],
)
def test_child_input_validation(client, collection, payload):
    location = create_location(client)
    assert (
        client.post(
            f"/api/v1/locations/{location['id']}/{collection}", json=payload
        ).status_code
        == 422
    )


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_pagination_validation(client, query):
    assert client.get(f"/api/v1/locations?{query}").status_code == 422
