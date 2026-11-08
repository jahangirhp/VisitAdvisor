"""Venue-authorized deletion, transactional cleanup and retry-safe boundaries."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

from app.db.mongodb import get_database
from app.modules.admin.router import service as get_photo_service
from app.modules.admin.service import PhotoService
from tests.test_admin_photos import (
    HEADERS,
    LOCATION,
    OBJECT,
    OTHER_LOCATION,
    PATH,
    SOURCE,
    Collection,
)
from tests.test_admin_photos import (
    setup as setup,
)

RELATED = (
    "visual_vectors",
    "visual_references",
    "knowledge_chunks",
    "admin_reference_photos",
)


class TransactionCollection(Collection):
    def __init__(self, name, rows, database):
        super().__init__(deepcopy(rows))
        self.name = name
        self.database = database

    async def find_one(self, query, projection=None, *, session=None):
        if session is not None:
            assert session is self.database.session
        return await super().find_one(query, projection)

    def deleting(self, session):
        assert session is self.database.session
        self.database.operations.append(self.name)
        if self.database.failure == self.name:
            raise OperationFailure("private-database-error-details")

    async def delete_many(self, query, *, session):
        self.deleting(session)
        return await super().delete_one(query)

    async def delete_one(self, query, *, session):
        self.deleting(session)
        return await super().delete_one(query)


class Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def with_transaction(self, callback, **options):
        assert options["write_concern"].document == {"w": "majority"}
        assert options["read_concern"].document == {"level": "snapshot"}
        before = {
            name: deepcopy(collection.rows) for name, collection in self.db.items()
        }
        try:
            return await callback(self)
        except BaseException:
            for name, rows in before.items():
                self.db[name].rows = rows
            raise


class Database(dict):
    def __init__(self, original):
        super().__init__()
        self.failure = None
        self.operations = []
        self.session = Session(self)
        self.client = SimpleNamespace(start_session=lambda: self.session)
        for name, collection in original.items():
            self[name] = TransactionCollection(name, collection.rows, self)
        self["knowledge_chunks"] = TransactionCollection("knowledge_chunks", [], self)


@pytest.fixture
def deletion(setup):
    client, _, original, *_ = setup
    db = Database(original)
    client.app.dependency_overrides[get_database] = lambda: db
    client.app.dependency_overrides[get_photo_service] = lambda: PhotoService(db)
    other = str(ObjectId())
    db["objects"].rows.append(
        {"_id": ObjectId(other), "location_id": LOCATION, "source_ids": [SOURCE]}
    )
    for name in RELATED:
        db[name].rows.extend(
            [
                {"object_id": OBJECT, "location_id": LOCATION, "version": version}
                for version in ("active", "previous", "staged")
            ]
            + [
                {"object_id": OBJECT, "location_id": OTHER_LOCATION},
                {"object_id": other, "location_id": LOCATION},
                {"object_id": other, "location_id": OTHER_LOCATION},
            ]
        )
    cache = SimpleNamespace(location_id=LOCATION, invalidate=AsyncMock())
    client.app.state.camera.state.references = cache
    client.app.state.visual_geometry_cache[OBJECT] = {"old": True}
    client.app.state.visual_geometry_cache[other] = {"keep": True}
    return client, db, cache, other


def snapshot(db):
    return {name: deepcopy(collection.rows) for name, collection in db.items()}


def test_deleting_object_removes_all_owned_data_and_preserves_shared_sources(deletion):
    client, db, cache, other = deletion
    sources = deepcopy(db["sources"].rows)
    response = client.delete(PATH, headers=HEADERS)
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert [str(row["_id"]) for row in db["objects"].rows] == [other]
    for name in RELATED:
        assert len(db[name].rows) == 2
        assert all(row["object_id"] == other for row in db[name].rows)
    assert db["sources"].rows == sources
    assert db["objects"].rows[0]["source_ids"] == [SOURCE]
    assert len(db["locations"].rows) == 1
    cache.invalidate.assert_awaited_once()
    assert OBJECT not in client.app.state.visual_geometry_cache
    assert other in client.app.state.visual_geometry_cache
    # A retry after a lost success response must not touch another object's data.
    before = snapshot(db)
    assert client.delete(PATH, headers=HEADERS).status_code == 404
    assert snapshot(db) == before


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer incorrect"}])
def test_delete_requires_admin_authorization(deletion, headers):
    client, db, cache, _ = deletion
    before = snapshot(db)
    assert client.delete(PATH, headers=headers).status_code == 401
    assert snapshot(db) == before
    assert db.operations == []
    cache.invalidate.assert_not_awaited()


@pytest.mark.parametrize(
    "path,code",
    [
        (PATH.replace(LOCATION, OTHER_LOCATION), 404),
        (PATH.replace(OBJECT, str(ObjectId())), 404),
        (PATH.replace(OBJECT, "invalid-id"), 422),
    ],
)
def test_delete_checks_object_ownership_and_ids_before_cleanup(deletion, path, code):
    client, db, cache, _ = deletion
    before = snapshot(db)
    assert client.delete(path, headers=HEADERS).status_code == code
    assert snapshot(db) == before
    assert db.operations == []
    cache.invalidate.assert_not_awaited()


@pytest.mark.parametrize("failure", [*RELATED, "objects"])
def test_failed_cleanup_rolls_back_every_collection_and_returns_safe_error(
    deletion, failure
):
    client, db, cache, _ = deletion
    before = snapshot(db)
    db.failure = failure
    response = client.delete(PATH, headers=HEADERS)
    assert response.status_code == 503, response.text
    assert "private-database-error-details" not in response.text
    assert response.json()["error"]["code"] == "OBJECT_DELETE_UNAVAILABLE"
    assert snapshot(db) == before
    cache.invalidate.assert_not_awaited()
    assert OBJECT in client.app.state.visual_geometry_cache
    db.failure = None
    assert client.delete(PATH, headers=HEADERS).status_code == 204


def test_delete_and_upload_reject_an_in_progress_publication(deletion):
    from tests.test_admin_photos import METADATA, jpeg

    client, db, _, _ = deletion
    before = snapshot(db)
    lock = client.app.state.admin_preparation_lock
    asyncio.run(lock.acquire())
    try:
        assert client.delete(PATH, headers=HEADERS).status_code == 409
        response = client.post(
            PATH + "/photos",
            headers=HEADERS,
            files={"image": ("photo.jpg", jpeg(), "image/jpeg")},
            data=METADATA,
        )
        assert response.status_code == 409, response.text
    finally:
        lock.release()
    assert snapshot(db) == before


def test_deletion_invalidates_only_affected_venue_caches(deletion):
    client, _, cache, _ = deletion
    unrelated = SimpleNamespace(
        location_id=OTHER_LOCATION, object_ids=[], invalidate=AsyncMock()
    )
    client.app.state.camera.state.venue_references = unrelated
    assert client.delete(PATH, headers=HEADERS).status_code == 204
    cache.invalidate.assert_awaited_once()
    unrelated.invalidate.assert_not_awaited()


def test_deletion_also_invalidates_a_cache_from_a_previous_venue(deletion):
    client, _, cache, _ = deletion
    cache.location_id = OTHER_LOCATION
    cache.object_ids = [OBJECT]
    assert client.delete(PATH, headers=HEADERS).status_code == 204
    cache.invalidate.assert_awaited_once()


def test_an_empty_object_can_be_deleted_without_loading_an_encoder(deletion):
    client, db, _, _ = deletion
    for name in RELATED:
        db[name].rows = []
    assert not hasattr(client.app.state, "admin_encoder")
    assert client.delete(PATH, headers=HEADERS).status_code == 204
    assert not hasattr(client.app.state, "admin_encoder")
