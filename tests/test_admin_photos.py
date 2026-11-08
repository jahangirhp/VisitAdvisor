"""Admin authentication, scoped uploads, duplicate handling and safe publication."""

import asyncio
import hashlib
import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import get_database
from app.main import create_app
from app.modules.admin.capture import (
    MAX_CAPTURE_METADATA_BYTES,
    MAX_FOREGROUND_MASK_BYTES,
)
from app.modules.admin.router import service as get_photo_service
from app.modules.admin.service import PhotoService
from app.modules.live_camera.pipeline import view_coverage
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
CAPTURE_METADATA = {
    "version": "guided-capture-v1",
    "session_id": "guided-session-1",
    "frame_id": "frame-1",
    "profile": "multiview",
    "requested_view": "front",
    "viewpoint_verified": False,
    "target_box": [0.1, 0.2, 0.8, 0.9],
    "quality": {
        "sharpness": 120.0,
        "clipped_fraction": 0.01,
        "target_fraction": 0.49,
    },
    "segmentation": {"model": "test-segmenter", "model_hash": "a" * 64},
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
        content=iter(
            [
                b"x"
                * (
                    MAX_REQUEST_BYTES
                    + MAX_CAPTURE_METADATA_BYTES
                    + MAX_FOREGROUND_MASK_BYTES
                    + 1
                )
            ]
        ),
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
        {"observer": " ", "reviewed": True},
        {"observer": "x" * 201, "reviewed": True},
        {"observer": "Jane", "reviewed": True, "type": "official_api"},
    ],
)
def test_manual_observation_rejects_invalid_review_and_observer(setup, observation):
    client, _, db, *_ = setup
    response = client.post(
        f"/api/v1/admin/venues/{LOCATION}/objects",
        headers=HEADERS,
        json={"name": "Object", "type": "painting", "observation": observation},
    )
    assert response.status_code == 422
    assert len(db["objects"].rows) == 1
    assert len(db["sources"].rows) == 1


def test_upload_automatically_uses_linked_source(setup):
    client, _, db, *_ = setup
    response = upload(client, source_id="")
    assert response.status_code == 201, response.text
    assert db["admin_reference_photos"].rows[0]["source_id"] == SOURCE


def test_upload_records_manual_test_association_when_no_source(setup):
    client, _, db, obj, _ = setup
    obj["source_ids"] = []

    async def link_source(query, update):
        obj["source_ids"].append(update["$addToSet"]["source_ids"])

    db["objects"].update_one = AsyncMock(side_effect=link_source)
    response = upload(client, source_id="")
    assert response.status_code == 201, response.text
    source = db["sources"].rows[-1]
    assert source["type"] == "manual"
    assert source["title"] == "Admin test photo association: Painting"
    assert db["admin_reference_photos"].rows[0]["source_id"] in obj["source_ids"]


def test_admin_test_upload_does_not_require_confirmation(setup):
    client, _, db, *_ = setup
    response = client.post(
        PATH + "/photos",
        headers=HEADERS,
        files={"image": ("photo.jpg", jpeg(), "image/jpeg")},
        data={key: value for key, value in METADATA.items() if key != "source_id"},
    )
    assert response.status_code == 201, response.text
    assert len(db["admin_reference_photos"].rows) == 1


def test_admin_creates_venue_with_only_name(setup):
    client, _, db, *_ = setup
    for name in ("  New venue  ", "New venue", "????"):
        response = client.post(
            "/api/v1/admin/venues", headers=HEADERS, json={"name": name}
        )
        assert response.status_code == 201, response.text
        venue = response.json()
        assert venue["name"] == name.strip()
        assert venue["type"] == "other"
        assert venue["city"] is None and venue["country"] is None
        assert venue["official_website"] is None
        assert venue["slug"].startswith("venue-")
        assert client.get(f"/api/v1/locations/{venue['id']}").status_code == 200
    slugs = [row.get("slug") for row in db["locations"].rows]
    assert len(slugs) == len(set(slugs))
    assert (
        client.post(
            "/api/v1/admin/venues", headers=HEADERS, json={"name": "   "}
        ).status_code
        == 422
    )


def test_admin_object_entry_does_not_require_or_infer_review(setup):
    client, _, db, *_ = setup
    db["locations"].rows[0].update(
        name="Museum",
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
            "name": "Statue",
            "type": "sculpture",
            "observation": {"observer": "Jane"},
        },
    )
    assert response.status_code == 201, response.text
    source = db["sources"].rows[-1]
    assert source["title"] == "Admin object entry: Statue"
    assert source["type"] == "manual"
    assert source["publisher"] == "Jane"


def test_photo_upload_does_not_require_photographer_name(setup):
    client, _, db, *_ = setup
    data = {key: value for key, value in METADATA.items() if key != "photographer"}
    response = client.post(
        PATH + "/photos",
        headers=HEADERS,
        data=data,
        files={"image": ("photo.jpg", jpeg(), "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    assert db["admin_reference_photos"].rows[-1]["photographer"] == "Not supplied"


@pytest.mark.parametrize(
    "capture",
    [
        "not json",
        "[]",
        json.dumps({**CAPTURE_METADATA, "version": "guided-capture-v2"}),
        json.dumps({**CAPTURE_METADATA, "official_source": "invented"}),
        json.dumps({**CAPTURE_METADATA, "session_id": "x" * 101}),
        json.dumps({**CAPTURE_METADATA, "frame_id": "bad/id"}),
        json.dumps({**CAPTURE_METADATA, "profile": "unknown"}),
        json.dumps({**CAPTURE_METADATA, "requested_view": "360-degrees"}),
        json.dumps({**CAPTURE_METADATA, "viewpoint_verified": "true"}),
        json.dumps({**CAPTURE_METADATA, "target_box": [-0.1, 0.2, 0.8, 0.9]}),
        json.dumps({**CAPTURE_METADATA, "target_box": [0.8, 0.2, 0.1, 0.9]}),
        json.dumps({**CAPTURE_METADATA, "target_box": [0.1, 0.2, 0.8, 0.2]}),
        json.dumps({**CAPTURE_METADATA, "target_box": [0.1, 0.2, 0.8]}),
        json.dumps(
            {
                **CAPTURE_METADATA,
                "quality": {**CAPTURE_METADATA["quality"], "sharpness": float("nan")},
            }
        ),
        json.dumps(
            {
                **CAPTURE_METADATA,
                "quality": {
                    **CAPTURE_METADATA["quality"],
                    "clipped_fraction": float("inf"),
                },
            }
        ),
        json.dumps(
            {
                **CAPTURE_METADATA,
                "quality": {**CAPTURE_METADATA["quality"], "target_fraction": 1.1},
            }
        ),
        json.dumps(
            {
                **CAPTURE_METADATA,
                "segmentation": {"model": "test-segmenter", "model_hash": "bad"},
            }
        ),
        json.dumps(
            {
                **CAPTURE_METADATA,
                "segmentation": {"model": "test-segmenter", "mask": "unvalidated"},
            }
        ),
        json.dumps(
            {**CAPTURE_METADATA, "untrusted": "\u20ac" * 11000}, ensure_ascii=False
        ),
    ],
)
def test_guided_capture_rejects_invalid_unbounded_or_invented_metadata(setup, capture):
    client, _, db, *_ = setup
    response = upload(client, capture_metadata=capture)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_CAPTURE_METADATA"
    assert db["admin_reference_photos"].rows == []


def test_guided_metadata_preserves_original_and_stays_private_until_preparation(setup):
    client, service, db, obj, source = setup
    image = jpeg()
    assert (
        upload(client, image, capture_metadata=json.dumps(CAPTURE_METADATA)).status_code
        == 201
    )
    photo = db["admin_reference_photos"].rows[0]
    assert photo["data"] == image
    assert photo["sha256"] == hashlib.sha256(image).hexdigest()
    assert photo["capture_metadata"] == CAPTURE_METADATA
    details = client.get(PATH, headers=HEADERS).json()
    assert details["photos"][0]["capture_metadata"] == CAPTURE_METADATA
    describe = AsyncMock()
    # Descriptors run in a synchronous encoder thread, never on a crop or mask.
    inputs = []
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda image, **_kw: inputs.append(image) or {"regions": []},
    )
    service.visual.publish = describe
    asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    reference = describe.await_args.args[0]["views"][0]["reference"]
    assert reference["evidence"]["capture_metadata"] == CAPTURE_METADATA
    assert reference["evidence"]["sha256"] == hashlib.sha256(image).hexdigest()
    assert reference["source_ids"] == [SOURCE]
    assert inputs == [prepare_image(image, "image/jpeg")]
    assert db["objects"].rows == [obj]
    assert db["sources"].rows == [source]


@pytest.mark.parametrize("confirmed", [False, True])
def test_requested_guided_angles_require_explicit_operator_review_for_coverage(
    setup, confirmed
):
    client, service, _, obj, _ = setup
    obj["type"] = "sculpture"
    for index, (color, angle) in enumerate([("red", "front"), ("blue", "back")]):
        capture = {
            **CAPTURE_METADATA,
            "frame_id": f"frame-{index}",
            "requested_view": angle,
        }
        if confirmed:
            capture["viewpoint_verified"] = True
        else:
            capture.pop("viewpoint_verified")
        response = upload(
            client, jpeg(color), viewpoint=angle, capture_metadata=json.dumps(capture)
        )
        assert response.status_code == 201, response.text
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda *_a, **_kw: {"regions": []},
    )
    service.visual.publish = AsyncMock()
    asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    views = service.visual.publish.await_args.args[0]["views"]
    coverage = view_coverage(
        {
            "type": "sculpture",
            "views": [
                {
                    "image_hash": v["image_hash"],
                    "viewpoint": v["reference"]["viewpoint"],
                }
                for v in views
            ],
        }
    )
    assert coverage["distinct_images"] == 2
    assert coverage["ready"] is confirmed
    assert coverage["distinct_viewpoints"] == (2 if confirmed else 0)
    assert [
        v["reference"]["evidence"]["capture_metadata"]["requested_view"] for v in views
    ] == ["front", "back"]


def png_mask(mode="L", size=(64, 64), foreground=255):
    image = Image.new(mode, size, (0, 0, 0, 255) if mode == "RGBA" else 0)
    ImageDraw.Draw(image).rectangle((16, 16, 47, 47), fill=foreground)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def upload_mask(client, mask, capture=CAPTURE_METADATA, content_type="image/png"):
    return client.post(
        PATH + "/photos",
        headers=HEADERS,
        files={
            "image": ("photo.jpg", jpeg(), "image/jpeg"),
            "foreground_mask": ("mask.png", mask, content_type),
        },
        data={
            **METADATA,
            **(
                {"capture_metadata": json.dumps(capture)} if capture is not None else {}
            ),
        },
    )


def test_foreground_mask_is_private_source_linked_and_excluded_from_descriptors(setup):
    client, service, db, *_ = setup
    response = upload_mask(client, png_mask())
    assert response.status_code == 201, response.text
    photo_id = response.json()["id"]
    photo = db["admin_reference_photos"].rows[0]
    assert photo["data"] == jpeg()
    assert (
        photo["foreground_mask"]["sha256"]
        == hashlib.sha256(photo["foreground_mask_data"]).hexdigest()
    )
    details = client.get(PATH, headers=HEADERS)
    assert details.status_code == 200
    assert details.json()["photos"][0]["foreground_mask"] == photo["foreground_mask"]
    assert "foreground_mask_data" not in details.text
    path = PATH + f"/photos/{photo_id}/mask"
    assert client.get(path).status_code == 401
    preview = client.get(path, headers=HEADERS)
    assert preview.status_code == 200
    assert preview.content == photo["foreground_mask_data"]
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "no-store"
    assert (
        client.get(path.replace(LOCATION, OTHER_LOCATION), headers=HEADERS).status_code
        == 404
    )
    inputs = []
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda image, **_kw: inputs.append(image) or {"regions": []},
    )
    service.visual.publish = AsyncMock()
    asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    evidence = service.visual.publish.await_args.args[0]["views"][0]["reference"][
        "evidence"
    ]
    assert evidence["foreground_mask"] == photo["foreground_mask"]
    assert inputs == [prepare_image(jpeg(), "image/jpeg")]


@pytest.mark.parametrize(
    "mode,foreground",
    [
        ("1", 255),
        ("L", 255),
        ("RGB", (255, 255, 255)),
        ("RGBA", (255, 255, 255, 255)),
    ],
)
def test_binary_masks_accept_grayscale_and_opaque_canvas_pngs(setup, mode, foreground):
    client, _, db, *_ = setup
    response = upload_mask(client, png_mask(mode=mode, foreground=foreground))
    assert response.status_code == 201, response.text
    stored = db["admin_reference_photos"].rows[0]
    with Image.open(io.BytesIO(stored["foreground_mask_data"])) as mask:
        assert mask.mode == "L"
        assert mask.size == (64, 64)
        assert mask.info == {}
        assert mask.getextrema() == (0, 255)


@pytest.mark.parametrize(
    "mask,content_type,capture,status",
    [
        (b"not png", "image/png", CAPTURE_METADATA, 422),
        (png_mask(), "image/jpeg", CAPTURE_METADATA, 415),
        (png_mask(size=(32, 32)), "image/png", CAPTURE_METADATA, 422),
        (png_mask(foreground=128), "image/png", CAPTURE_METADATA, 422),
        (png_mask(foreground=0), "image/png", CAPTURE_METADATA, 422),
        (
            png_mask(mode="RGB", foreground=(255, 0, 0)),
            "image/png",
            CAPTURE_METADATA,
            422,
        ),
        (
            png_mask(mode="RGBA", foreground=(255, 255, 255, 128)),
            "image/png",
            CAPTURE_METADATA,
            422,
        ),
        (b"x" * (512 * 1024 + 1), "image/png", CAPTURE_METADATA, 413),
        (png_mask(), "image/png", None, 422),
    ],
)
def test_invalid_masks_are_rejected_without_saving_source(
    setup, mask, content_type, capture, status
):
    client, _, db, *_ = setup
    response = upload_mask(client, mask, capture, content_type)
    assert response.status_code == status, response.text
    assert db["admin_reference_photos"].rows == []


def test_manual_photos_have_no_mask_or_capture_metadata(setup):
    client, _, db, *_ = setup
    response = upload(client)
    assert response.status_code == 201
    photo_id = response.json()["id"]
    assert "capture_metadata" not in db["admin_reference_photos"].rows[0]
    assert "foreground_mask" not in db["admin_reference_photos"].rows[0]
    assert (
        client.get(PATH + f"/photos/{photo_id}/mask", headers=HEADERS).status_code
        == 404
    )


def reviewed_capture(method="manual-box"):
    capture = deepcopy(CAPTURE_METADATA)
    capture.update(
        version="guided-capture-v2",
        selection={
            "method": method,
            "reviewed": True,
            "source_sha256": hashlib.sha256(jpeg()).hexdigest(),
        },
    )
    capture.pop("segmentation")
    if method == "full-frame":
        capture["target_box"] = [0, 0, 1, 1]
    else:
        capture["selection"]["mask_sha256"] = hashlib.sha256(png_mask()).hexdigest()
    return capture


@pytest.mark.parametrize("method", ["manual-box", "full-frame", "server-mask"])
def test_reviewed_selection_preserves_source_and_truthful_derivative_provenance(
    setup, method
):
    client, service, db, *_ = setup
    capture = reviewed_capture(method)
    if method == "server-mask":
        identity = {"model": "test-sam2", "revision": "pinned-revision"}
        capture["segmentation"] = identity
        capture["analysis"] = {
            **identity,
            "source_sha256": capture["selection"]["source_sha256"],
            "prompt_point": [0.4, 0.5],
            "prompt_box": [0.1, 0.2, 0.8, 0.9],
        }
        capture["selection"].update(
            candidate_id="mask-2",
            mask_sha256=hashlib.sha256(png_mask()).hexdigest(),
        )
    response = (
        upload(client, capture_metadata=json.dumps(capture))
        if method == "full-frame"
        else upload_mask(client, png_mask(), capture)
    )
    assert response.status_code == 201, response.text
    photo = db["admin_reference_photos"].rows[0]
    assert photo["data"] == jpeg()
    assert photo["capture_metadata"] == capture
    inputs = []
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda image, **_kw: inputs.append(image) or {"regions": []},
    )
    service.visual.publish = AsyncMock()
    asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    assert inputs == [prepare_image(photo["data"], "image/jpeg")]


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed": False},
        {"source_sha256": "b" * 64},
        {"mask_sha256": "c" * 64},
        {"candidate_id": "invented-model-candidate"},
    ],
)
def test_reviewed_selection_rejects_unreviewed_or_mismatched_derivatives(setup, change):
    client, _, db, *_ = setup
    capture = reviewed_capture()
    capture["selection"].update(change)
    response = upload_mask(client, png_mask(), capture)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "INVALID_CAPTURE_METADATA"
    assert db["admin_reference_photos"].rows == []


def test_reviewed_box_cannot_be_uploaded_without_its_mask(setup):
    client, _, db, *_ = setup
    response = upload(client, capture_metadata=json.dumps(reviewed_capture()))
    assert response.status_code == 422
    assert db["admin_reference_photos"].rows == []


def quick_capture(status="pending"):
    capture = reviewed_capture("full-frame")
    capture.update(
        version="guided-capture-v3",
        quality_region="full-frame",
        detection_status=status,
    )
    capture.pop("requested_view")
    capture["selection"]["reviewed"] = False
    if status == "suggested":
        identity = {
            "model": "test-sam2",
            "revision": "pinned-revision",
            "model_hash": "a" * 64,
        }
        capture["segmentation"] = identity
        capture["analysis"] = {
            **identity,
            "source_sha256": capture["selection"]["source_sha256"],
            "prompt_point": [0.5, 0.5],
        }
        capture["target_box"] = [0.1, 0.2, 0.8, 0.9]
        capture["selection"].update(
            method="server-suggestion",
            candidate_id="mask-0",
            mask_sha256=hashlib.sha256(png_mask()).hexdigest(),
        )
    return capture


@pytest.mark.parametrize("status", ["pending", "suggested", "unavailable", "skipped"])
def test_quick_capture_keeps_original_and_leaves_angle_unverified(setup, status):
    client, service, db, *_ = setup
    capture = quick_capture(status)
    if status == "suggested":
        response = upload_mask(client, png_mask(), capture)
    else:
        response = upload(
            client, viewpoint="unknown", capture_metadata=json.dumps(capture)
        )
    assert response.status_code == 201, response.text
    photo = db["admin_reference_photos"].rows[0]
    assert photo["data"] == jpeg()
    assert photo["capture_metadata"] == capture
    assert photo["capture_metadata"]["selection"]["reviewed"] is False
    inputs = []
    encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda image, **_kw: inputs.append(image) or {"regions": []},
    )
    service.visual.publish = AsyncMock()
    asyncio.run(service.publish(LOCATION, OBJECT, encoder))
    reference = service.visual.publish.await_args.args[0]["views"][0]["reference"]
    assert reference["viewpoint"] is None
    assert reference["evidence"]["capture_metadata"] == capture
    assert inputs == [prepare_image(photo["data"], "image/jpeg")]


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "b" * 64},
        {"mask_sha256": "c" * 64},
        {"candidate_id": None},
    ],
)
def test_quick_suggestion_rejects_missing_or_mismatched_provenance(setup, change):
    client, _, db, *_ = setup
    capture = quick_capture("suggested")
    capture["selection"].update(change)
    response = upload_mask(client, png_mask(), capture)
    assert response.status_code == 422, response.text
    assert db["admin_reference_photos"].rows == []


@pytest.mark.parametrize("missing", ["model_hash", "revision"])
def test_quick_suggestion_requires_pinned_model_identity(setup, missing):
    client, _, db, *_ = setup
    capture = quick_capture("suggested")
    capture["segmentation"].pop(missing)
    capture["analysis"].pop(missing)
    response = upload_mask(client, png_mask(), capture)
    assert response.status_code == 422
    assert db["admin_reference_photos"].rows == []


def test_quick_full_frame_still_checks_source_hash(setup):
    client, _, db, *_ = setup
    capture = quick_capture()
    capture["selection"]["source_sha256"] = "b" * 64
    response = upload(client, capture_metadata=json.dumps(capture))
    assert response.status_code == 422
    assert db["admin_reference_photos"].rows == []


def test_collection_save_embeds_every_photo_and_retry_reuses_the_published_set(setup):
    client, service, db, *_ = setup
    for color in ("red", "blue", "green", "yellow"):
        assert upload(client, jpeg(color)).status_code == 201
    inputs = []
    client.app.state.admin_encoder = SimpleNamespace(
        model="local-model",
        preprocessing="local-preprocess",
        describe=lambda image, **kwargs: (
            inputs.append(image) or {"regions": [{"embedding": [1.0] + [0.0] * 383}]}
        ),
    )

    async def publish(record, previous):
        db["visual_references"].rows = [deepcopy(record)]

    service.visual.publish = AsyncMock(side_effect=publish)
    assert client.post(PATH + "/save").status_code == 401
    response = client.post(PATH + "/save", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["prepared"] == 4
    assert len(inputs) == 4
    assert {hashlib.sha256(image).hexdigest() for image in inputs} == {
        p["normalized_sha256"] for p in db["admin_reference_photos"].rows
    }
    record = db["visual_references"].rows[0]
    assert len(record["views"]) == record["vector_count"] == 4
    assert client.get(PATH, headers=HEADERS).json()["pending"] == 0
    retry = client.post(PATH + "/save", headers=HEADERS)
    assert retry.json() == response.json()
    assert len(inputs) == 4
    assert service.visual.publish.await_count == 1
    assert len(db["admin_reference_photos"].rows) == 4
    # Preserve the old explicit API's no-pending response for existing clients.
    assert client.post(PATH + "/prepare", headers=HEADERS).status_code == 409


def test_collection_save_failure_keeps_photos_and_allows_retry_without_reupload(setup):
    client, service, db, *_ = setup
    assert upload(client).status_code == 201

    def unavailable(*args, **kwargs):
        raise VisualFailure("VISUAL_MODEL_UNAVAILABLE")

    client.app.state.admin_encoder = SimpleNamespace(
        model="local-model", preprocessing="local-preprocess", describe=unavailable
    )
    response = client.post(PATH + "/save", headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    assert len(db["admin_reference_photos"].rows) == 1
    assert db["visual_references"].rows == []
    client.app.state.admin_encoder.describe = lambda *args, **kwargs: {"regions": []}
    service.visual.publish = AsyncMock()
    assert client.post(PATH + "/save", headers=HEADERS).json()["prepared"] == 1
    assert len(db["admin_reference_photos"].rows) == 1
