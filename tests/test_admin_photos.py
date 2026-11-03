"""Admin authentication, scoped uploads, duplicate handling and safe publication."""

import asyncio
import hashlib
import io
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import get_database
from app.main import create_app
from app.modules.admin.router import service as get_photo_service
from app.modules.admin.service import PhotoService
from app.modules.recognition.images import MAX_REQUEST_BYTES, prepare_image
from app.modules.visual.features import VisualFailure
from app.modules.visual.repository import evidence_hash

LOCATION, OBJECT, SOURCE = [str(ObjectId()) for _ in range(3)]
OTHER_LOCATION = str(ObjectId())


def jpeg(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(stream, "JPEG")
    return stream.getvalue()


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, key, order):
        self.rows.sort(key=lambda r: r.get(key, ""), reverse=order == -1)
        return self

    def skip(self, offset):
        self.rows = self.rows[offset:]
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self):
        return self.rows


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.insert_error = None

    def find(self, query, projection=None):
        rows = [
            r
            for r in self.rows
            if all(
                r.get(k) in v["$in"] if isinstance(v, dict) else r.get(k) == v
                for k, v in query.items()
            )
        ]
        if projection:
            rows = [
                {
                    k: v
                    for k, v in r.items()
                    if k == "_id"
                    or projection.get(k)
                    or any(p.startswith(k + ".") for p in projection)
                }
                for r in rows
            ]
        return Cursor(rows)

    async def find_one(self, query, projection=None):
        rows = await self.find(query, projection).to_list()
        return rows[0] if rows else None

    async def count_documents(self, query):
        return len(await self.find(query).to_list())

    async def insert_one(self, row):
        if self.insert_error:
            raise self.insert_error
        if row.get("normalized_sha256") is not None and any(
            r.get("normalized_sha256") == row.get("normalized_sha256")
            for r in self.rows
        ):
            raise DuplicateKeyError("duplicate")
        row = {"_id": ObjectId(), **row}
        self.rows.append(deepcopy(row))
        return SimpleNamespace(inserted_id=row["_id"])

    async def delete_one(self, query):
        rows = await self.find(query).to_list()
        self.rows = [r for r in self.rows if r not in rows]
        return SimpleNamespace(deleted_count=len(rows))


@pytest.fixture
def setup():
    obj = {
        "_id": ObjectId(OBJECT),
        "location_id": LOCATION,
        "name": "Painting",
        "type": "painting",
        "source_ids": [SOURCE],
    }
    source = {
        "_id": ObjectId(SOURCE),
        "location_id": LOCATION,
        "title": "Museum record",
        "raw_record": {"identity": "canonical"},
    }
    db = {
        name: Collection(rows)
        for name, rows in {
            "objects": [obj],
            "sources": [source],
            "visual_references": [],
            "visual_vectors": [],
            "admin_reference_photos": [],
            "locations": [{"_id": ObjectId(LOCATION), "name": "Museum"}],
        }.items()
    }
    service = PhotoService(db)
    app = create_app()
    app.state.settings = Settings(
        mongodb_uri="mongodb://localhost", admin_token="test-token"
    )
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_photo_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)
    yield client, service, db, obj, source
    client.close()


HEADERS = {"Authorization": "Bearer test-token"}
PATH = f"/api/v1/admin/venues/{LOCATION}/objects/{OBJECT}"
METADATA = {
    "viewpoint": "front",
    "photographer": "Museum team",
    "capture_group": "visit-1",
    "rights": "Own photograph; permitted reference use",
    "source_id": SOURCE,
}


def upload(client, image=None, **overrides):
    return client.post(
        PATH + "/photos",
        headers=HEADERS,
        files={"image": ("photo.jpg", image or jpeg(), "image/jpeg")},
        data={**METADATA, "reviewed": "true", **overrides},
    )


def test_admin_requires_auth_and_defaults_disabled(setup):
    client, *_ = setup
    assert client.get("/api/v1/admin/venues").status_code == 401
    assert (
        client.get(
            "/api/v1/admin/venues", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    client.app.state.settings.admin_token = None
    assert client.get("/api/v1/admin/venues", headers=HEADERS).status_code == 503
    assert client.get("/admin/").status_code == 200
    assert "test-token" not in client.get("/admin/").text


def test_saved_upload_is_private_and_not_yet_a_reference(setup):
    client, _, db, *_ = setup
    response = upload(client)
    assert response.status_code == 201, response.text
    photo_id = response.json()["id"]
    assert len(db["admin_reference_photos"].rows) == 1
    assert db["visual_references"].rows == []
    detail = client.get(PATH, headers=HEADERS).json()
    assert (detail["prepared"], detail["pending"], detail["missing"]) == (0, 1, 2)
    assert "data" not in detail["photos"][0]
    assert client.get(PATH + f"/photos/{photo_id}").status_code == 401
    image = client.get(PATH + f"/photos/{photo_id}", headers=HEADERS)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "no-store"
    assert (
        client.get(
            PATH.replace(LOCATION, OTHER_LOCATION) + f"/photos/{photo_id}",
            headers=HEADERS,
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"reviewed": "false"}, "REVIEW_REQUIRED"),
        ({"viewpoint": "  "}, "REVIEW_REQUIRED"),
        ({"source_id": str(ObjectId())}, "SOURCE_MISMATCH"),
    ],
)
def test_review_and_object_source_are_required(setup, overrides, code):
    response = upload(setup[0], **overrides)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert setup[2]["admin_reference_photos"].rows == []


def test_invalid_image_and_oversized_chunked_body_rejected(setup):
    client, _, db, *_ = setup
    assert upload(client, b"not a photo").status_code == 422
    response = client.post(
        PATH + "/photos",
        headers=HEADERS,
        content=iter([b"x" * (MAX_REQUEST_BYTES + 1)]),
    )
    assert response.status_code == 413
    assert db["admin_reference_photos"].rows == []


def test_duplicate_upload_and_concurrent_slot_conflict(setup):
    client, _, db, *_ = setup
    assert upload(client).status_code == 201
    assert upload(client).status_code == 409
    db["admin_reference_photos"].insert_error = DuplicateKeyError(
        "secret database details"
    )
    response = upload(client, jpeg("blue"))
    assert response.status_code == 409
    assert "secret" not in response.text


def test_wrong_venue_cannot_receive_upload(setup):
    client, *_ = setup
    response = client.post(
        PATH.replace(LOCATION, OTHER_LOCATION) + "/photos",
        headers=HEADERS,
        files={"image": ("p.jpg", jpeg(), "image/jpeg")},
        data={**METADATA, "reviewed": "true"},
    )
    assert response.status_code == 404


def test_inventory_counts_unique_images_and_configurable_targets(setup):
    client, service, db, obj, source = setup
    db["visual_references"].rows.append(
        {
            "object_id": OBJECT,
            "location_id": LOCATION,
            "evidence_hash": evidence_hash(obj, [source]),
            "views": [{"image_hash": "same"}, {"image_hash": "same"}],
        }
    )
    service.targets = (2, 4)
    response = client.get(f"/api/v1/admin/venues/{LOCATION}/objects", headers=HEADERS)
    row = response.json()["objects"][0]
    assert (row["prepared"], row["target"], row["missing"]) == (1, 2, 1)
    db["sources"].rows[0]["raw_record"] = {"identity": "changed"}
    response = client.get(f"/api/v1/admin/venues/{LOCATION}/objects", headers=HEADERS)
    assert response.json()["objects"][0]["prepared"] == 0


def test_prepare_preserves_previous_views_and_records_photo_provenance(setup):
    client, service, db, obj, source = setup
    assert upload(client).status_code == 201
    old_view = {
        "image_hash": "existing",
        "reference": {"source_ids": [SOURCE]},
        "derived": {"regions": [{"embedding": [1]}]},
    }
    previous = {
        "object_id": OBJECT,
        "location_id": LOCATION,
        "set_id": "old",
        "model": "local-model",
        "preprocessing": "local-preprocess",
        "evidence_hash": evidence_hash(obj, [source]),
        "views": [old_view],
    }
    db["visual_references"].rows.append(previous)
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda *_a, **_kw: {"regions": [{"embedding": [2]}]},
    )
    service.visual.publish = AsyncMock()
    result = asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    record, old = service.visual.publish.await_args.args
    assert record["set_id"] != old["set_id"]
    assert record["views"][0] == old_view
    assert len(record["views"]) == 2
    assert record["views"][1]["reference"]["evidence"]["capture_group"] == "visit-1"
    assert record["views"][1]["reference"]["source_ids"] == [SOURCE]
    assert result["prepared"] == 2
    assert db["objects"].rows == [obj]
    assert db["sources"].rows == [source]


def test_preparation_failure_leaves_old_set_and_upload_intact(setup):
    client, service, db, obj, source = setup
    assert upload(client).status_code == 201
    old = {
        "location_id": LOCATION,
        "object_id": OBJECT,
        "set_id": "old",
        "model": "m",
        "preprocessing": "p",
        "views": [],
        "evidence_hash": evidence_hash(obj, [source]),
    }
    db["visual_references"].rows.append(old)
    encoder = SimpleNamespace(
        model="m", preprocessing="p", describe=lambda *_a, **_kw: {"regions": []}
    )
    service.visual.publish = AsyncMock(
        side_effect=VisualFailure("REFERENCE_SET_CHANGED")
    )
    with pytest.raises(ApplicationError, match="References changed"):
        asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    assert db["visual_references"].rows[0] == old
    assert len(db["admin_reference_photos"].rows) == 1


def test_existing_four_views_block_upload_and_prepared_upload_not_pending(setup):
    client, service, db, obj, source = setup
    image = jpeg()
    clean = prepare_image(image, "image/jpeg")
    db["visual_references"].rows.append(
        {
            "location_id": LOCATION,
            "object_id": OBJECT,
            "evidence_hash": evidence_hash(obj, [source]),
            "views": [
                {
                    "image_hash": hashlib.sha256(image).hexdigest(),
                    "normalized_image_hash": hashlib.sha256(clean).hexdigest(),
                }
            ]
            + [{"image_hash": str(i)} for i in range(3)],
        }
    )
    assert upload(client, jpeg("blue")).status_code == 409
    assert upload(client, image).json()["error"]["code"] == "DUPLICATE_PHOTO"
    row = service.coverage(
        obj,
        db["visual_references"].rows[0],
        [
            {
                "sha256": hashlib.sha256(image).hexdigest(),
                "normalized_sha256": hashlib.sha256(clean).hexdigest(),
            }
        ],
    )
    assert row["pending"] == 0


def test_unpublished_upload_can_be_removed_but_published_cannot(setup):
    client, _, db, *_ = setup
    photo_id = upload(client).json()["id"]
    db["visual_references"].rows.append(
        {
            "location_id": LOCATION,
            "object_id": OBJECT,
            "views": [{"reference": {"evidence": {"upload_id": photo_id}}}],
        }
    )
    path = PATH + f"/photos/{photo_id}"
    assert client.delete(path, headers=HEADERS).status_code == 409
    db["visual_references"].rows.clear()
    assert client.delete(path, headers=HEADERS).status_code == 204
    assert db["admin_reference_photos"].rows == []


def test_admin_create_venue_requires_auth_and_validates_catalog_fields(setup):
    client, _, db, *_ = setup
    data = {
        "name": "New museum",
        "slug": "new-museum",
        "type": "museum",
        "city": "Amsterdam",
        "country": "Netherlands",
    }
    assert client.post("/api/v1/admin/venues", json=data).status_code == 401
    assert len(db["locations"].rows) == 1
    assert (
        client.post(
            "/api/v1/admin/venues", headers=HEADERS, json={**data, "slug": "bad slug"}
        ).status_code
        == 422
    )
    db["locations"].rows.clear()
    response = client.post("/api/v1/admin/venues", headers=HEADERS, json=data)
    assert response.status_code == 201, response.text
    venue = response.json()
    assert venue["name"] == data["name"]
    assert venue["id"] == str(db["locations"].rows[0]["_id"])
    assert (
        client.get("/api/v1/admin/venues", headers=HEADERS).json()[0]["id"]
        == venue["id"]
    )
    db["locations"].insert_error = DuplicateKeyError("duplicate")
    assert (
        client.post("/api/v1/admin/venues", headers=HEADERS, json=data).status_code
        == 409
    )


@pytest.mark.parametrize("new_venue", [False, True])
def test_admin_creates_object_in_existing_or_new_venue(setup, new_venue):
    client, _, db, *_ = setup
    venue = {
        "name": "Museum",
        "slug": "museum",
        "type": "museum",
        "city": "Amsterdam",
        "country": "Netherlands",
    }
    if new_venue:
        db["locations"].rows.clear()
        response = client.post("/api/v1/admin/venues", headers=HEADERS, json=venue)
        assert response.status_code == 201
        venue_id = response.json()["id"]
    else:
        venue_id = LOCATION
        db["locations"].rows[0].update(
            **venue, created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
        )
    path = f"/api/v1/admin/venues/{venue_id}/objects"
    data = {"name": "New sculpture", "type": "sculpture", "year": "17th century"}
    before = len(db["objects"].rows)
    assert client.post(path, json=data).status_code == 401
    assert (
        client.post(path, headers=HEADERS, json={**data, "name": " "}).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=HEADERS, json={**data, "location_id": OTHER_LOCATION}
        ).status_code
        == 422
    )
    assert len(db["objects"].rows) == before
    response = client.post(path, headers=HEADERS, json=data)
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["location_id"] == venue_id
    assert record["name"] == data["name"]
    assert record["source_ids"] == []
    assert len(db["objects"].rows) == before + 1
    assert db["objects"].rows[-1]["location_id"] == venue_id
    detail = client.get(path + "/" + record["id"], headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json()["name"] == data["name"]


def test_admin_object_creation_rejects_missing_venue_and_foreign_sources(setup):
    client, _, db, *_ = setup
    data = {"name": "Object", "type": "painting"}
    path = f"/api/v1/admin/venues/{OTHER_LOCATION}/objects"
    assert client.post(path, headers=HEADERS, json=data).status_code == 404
    assert (
        client.post(
            "/api/v1/admin/venues/invalid/objects", headers=HEADERS, json=data
        ).status_code
        == 422
    )
    db["locations"].rows[0].update(
        slug="museum",
        type="museum",
        city="Amsterdam",
        country="Netherlands",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db["sources"].rows[0]["location_id"] = OTHER_LOCATION
    response = client.post(
        f"/api/v1/admin/venues/{LOCATION}/objects",
        headers=HEADERS,
        json={**data, "source_ids": [SOURCE]},
    )
    assert response.status_code == 422
    assert len(db["objects"].rows) == 1


def test_photo_first_creation_records_manual_provenance_and_accepts_photos(setup):
    client, _, db, *_ = setup
    db["locations"].rows[0].update(
        slug="museum",
        type="museum",
        city="Amsterdam",
        country="Netherlands",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    response = client.post(
        f"/api/v1/admin/venues/{LOCATION}/objects",
        headers=HEADERS,
        json={
            "name": "Bronze statue",
            "type": "sculpture",
            "observation": {"observer": "  Jane Smith  ", "reviewed": True},
        },
    )
    assert response.status_code == 201, response.text
    obj = response.json()
    assert "observation" not in obj
    source_id = obj["source_ids"][0]
    source = next(row for row in db["sources"].rows if str(row["_id"]) == source_id)
    assert source["type"] == "manual"
    assert source["publisher"] == "Jane Smith"
    assert source["title"] == "On-site identification: Bronze statue"
    assert source["location_id"] == LOCATION
    assert source["url"] is None
    photo_path = f"/api/v1/admin/venues/{LOCATION}/objects/{obj['id']}/photos"
    for color, viewpoint in [("red", "front"), ("blue", "left")]:
        uploaded = client.post(
            photo_path,
            headers=HEADERS,
            files={"image": ("reference.jpg", jpeg(color), "image/jpeg")},
            data={
                **METADATA,
                "source_id": source_id,
                "viewpoint": viewpoint,
                "reviewed": "true",
            },
        )
        assert uploaded.status_code == 201, uploaded.text
    assert len(db["admin_reference_photos"].rows) == 2
    assert db["visual_references"].rows == []


@pytest.mark.parametrize(
    "observation",
    [
        {"observer": "Jane", "reviewed": False},
        {"observer": "Jane"},
        {"observer": " ", "reviewed": True},
        {"observer": "x" * 201, "reviewed": True},
        {"observer": "Jane", "reviewed": True, "type": "official_api"},
    ],
)
def test_manual_observation_needs_explicit_review_and_named_observer(
    setup, observation
):
    client, _, db, *_ = setup
    response = client.post(
        f"/api/v1/admin/venues/{LOCATION}/objects",
        headers=HEADERS,
        json={"name": "Object", "type": "painting", "observation": observation},
    )
    assert response.status_code == 422
    assert len(db["objects"].rows) == 1
    assert len(db["sources"].rows) == 1
