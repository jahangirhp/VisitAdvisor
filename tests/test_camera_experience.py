"""Camera content stays scoped, source-linked, read-only and sanitized."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.modules.live_camera.server import create_camera_app
from tests.test_camera_references import atlas  # noqa: F401


def prepare_object(db):
    obj = db["objects"].rows[0]
    evidence = {str(s["_id"]): s["raw_record"] for s in db["sources"].rows}
    sid = next(iter(evidence))
    # The actual source is canonical; the fixture does not invoke generation.
    section = {
        "text": "Prepared introduction",
        "citations": [{"source_id": sid, "quote": "original"}],
    }
    obj["prepared_content"] = {
        "content": {
            "introduction": section,
            "facts": [deepcopy(section)],
            "story": None,
            "history": None,
            "interesting_fact": None,
        },
        "source_ids": [sid],
        "evidence_hash": hashlib.sha256(
            json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "provider": "fixture",
        "model": "fixture",
        "requested_model": "fixture",
        "prompt_version": "fixture",
        "generated_at": datetime.now(UTC),
        "input_tokens": 0,
        "output_tokens": 0,
        "review_status": "unreviewed",
    }
    return obj


@pytest.fixture
def content_camera(atlas):  # noqa: F811 - imported pytest fixture
    loader, db, _, _ = atlas
    obj = prepare_object(db)
    app = create_camera_app()
    app.state.references = loader
    app.state.mongodb = SimpleNamespace(database=db)
    with TestClient(app) as client:
        public = client.get("/bundle/catalog.json").json()
        url = (
            f"/objects/{obj['_id']}/experience"
            f"?location_id={public['location']['id']}"
            f"&reference_version={public['reference_version']}"
        )
        yield client, url, obj, db, loader


def test_content_is_prepared_scoped_and_excludes_private_metadata(content_camera):
    client, url, obj, _, _ = content_camera
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["object_id"] == str(obj["_id"])
    assert data["content"]["introduction"]["text"] == "Prepared introduction"
    assert data["review_status"] == "unreviewed"
    assert response.headers["cache-control"] == "no-store"
    assert set(data) == {
        "object_id",
        "location_id",
        "reference_version",
        "content",
        "source_ids",
        "review_status",
    }
    assert client.post(url).status_code == 405
    assert client.get("/api/v1/objects").status_code == 404


@pytest.mark.parametrize(
    "case,code",
    [
        ("location", 409),
        ("version", 409),
        ("outside", 404),
        ("malformed_id", 404),
        ("missing", 404),
        ("moved", 404),
        ("foreign_source", 503),
        ("foreign_citation", 503),
        ("outdated", 409),
        ("invalid_content", 503),
        ("deleted_source", 503),
        ("expired", 503),
        ("foreign_quote", 503),
        ("source_moved", 503),
    ],
)
def test_content_rejects_invalid_or_unavailable_evidence(content_camera, case, code):
    client, url, obj, db, loader = content_camera
    if case == "location":
        url = url.replace(obj["location_id"], "f" * 24)
    elif case == "version":
        url = url.replace(loader.recognizer.reference_version, "f" * 64)
    elif case == "outside":
        url = url.replace(str(obj["_id"]), str(ObjectId()))
    elif case == "malformed_id":
        url = url.replace(str(obj["_id"]), "invalid")
    elif case == "missing":
        obj["prepared_content"] = None
    elif case == "moved":
        obj["location_id"] = str(ObjectId())
    elif case == "foreign_source":
        obj["prepared_content"]["source_ids"] = [str(ObjectId())]
    elif case == "foreign_citation":
        obj["prepared_content"]["content"]["introduction"]["citations"][0][
            "source_id"
        ] = str(ObjectId())
    elif case == "foreign_quote":
        obj["prepared_content"]["content"]["introduction"]["citations"][0]["quote"] = (
            "Not in evidence"
        )
    elif case == "source_moved":
        db["sources"].rows[0]["location_id"] = str(ObjectId())
    elif case == "outdated":
        db["sources"].rows[0]["raw_record"] = {"changed": True}
    elif case == "invalid_content":
        obj["prepared_content"]["content"]["introduction"] = {"text": "uncited"}
    elif case == "deleted_source":
        db["sources"].rows.clear()
    elif case == "expired":
        from datetime import timedelta

        loader.recognizer.expires = datetime.now(UTC) - timedelta(days=1)
    response = client.get(url)
    assert response.status_code == code
    assert "Prepared introduction" not in response.text


def test_content_database_failure_is_sanitized(content_camera, monkeypatch):
    client, url, _, db, _ = content_camera
    monkeypatch.setattr(
        db["objects"],
        "find_one",
        AsyncMock(side_effect=RuntimeError("mongodb://private:secret@host")),
    )
    response = client.get(url)
    assert response.status_code == 503
    assert response.json() == {"error": "CONTENT_UNAVAILABLE"}


def test_reference_change_during_content_read_discards_response(
    content_camera, monkeypatch
):
    client, url, _, db, loader = content_camera
    original = db["objects"].find_one

    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        db["visual_references"].rows[0]["set_id"] = "replacement"
        loader.checked_until = 0
        return result

    monkeypatch.setattr(db["objects"], "find_one", changing)
    response = client.get(url)
    assert response.status_code == 409
    assert response.json() == {"error": "REFERENCES_CHANGED"}


def test_content_route_and_assets_work_under_live_mount(content_camera):
    from fastapi import FastAPI

    client, url, obj, _, _ = content_camera
    parent = FastAPI()
    parent.mount("/live", client.app)
    with TestClient(parent) as mounted:
        assert mounted.get("/live/").status_code == 200
        assert mounted.get("/live/experience.mjs").status_code == 200
        response = mounted.get("/live" + url)
        assert response.status_code == 200
        assert response.json()["object_id"] == str(obj["_id"])
