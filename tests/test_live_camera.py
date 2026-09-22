"""Privacy boundary and provenance checks; no provider calls or database writes."""

from copy import deepcopy

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.modules.live_camera.export import catalog_object, public_url
from app.modules.live_camera.server import create_camera_app
from app.modules.visual.features import VisualFailure
from app.modules.visual.repository import evidence_hash


def test_camera_host_only_serves_allowlisted_downloads(tmp_path):
    (tmp_path / "catalog.json").write_text('{"example": true}')
    (tmp_path / "private.json").write_text('"secret"')
    client = TestClient(create_camera_app(tmp_path))
    page = client.get("/")
    assert page.status_code == 200
    assert "camera=(self)" in page.headers["permissions-policy"]
    assert "connect-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/bundle/catalog.json").status_code == 503
    assert client.get("/bundle/private.json").status_code == 404
    assert client.get("/bundle/model.onnx").json() == {"error": "NOT_FOUND"}
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/", "/bundle/catalog.json", "/api/v1/recognition/image"):
            assert client.request(method, path, content=b"frame").status_code == 405
    assert client.get("/api/v1/locations").status_code == 404
    assert client.get("/docs").status_code == 404


def export_fixture():
    oid, sid = ObjectId(), ObjectId()
    location = str(ObjectId())
    obj = {
        "_id": oid,
        "location_id": location,
        "name": "Example painting",
        "type": "painting",
        "source_ids": [str(sid)],
        "prepared_content": {"story": "not canonical"},
    }
    sources = [
        {
            "_id": sid,
            "location_id": location,
            "title": "Museum",
            "url": "https://example.org/object",
            "raw_record": {"evidence": "original"},
        }
    ]
    record = {
        "object_id": str(oid),
        "location_id": location,
        "set_id": "set-version",
        "evidence_hash": evidence_hash(obj, sources),
        "views": [
            {
                "reference": {"source_ids": [str(sid)]},
                "derived": {"regions": [{"embedding": [1.0] + [0.0] * 383}]},
            }
        ],
    }
    return obj, sources, record


def test_export_preserves_provenance_without_raw_or_generated_content():
    obj, sources, record = export_fixture()
    result = catalog_object(obj, sources, record)
    assert result["reference_set_id"] == "set-version"
    assert result["sources"][0]["id"] == str(sources[0]["_id"])
    assert "raw_record" not in result["sources"][0]
    assert "prepared_content" not in result


@pytest.mark.parametrize("change", ["evidence", "location", "source", "vector"])
def test_export_rejects_stale_cross_location_and_invalid_references(change):
    obj, sources, record = deepcopy(export_fixture())
    if change == "evidence":
        sources[0]["raw_record"] = {"evidence": "changed"}
    elif change == "location":
        obj["location_id"] = str(ObjectId())
    elif change == "source":
        sources[0]["location_id"] = str(ObjectId())
    else:
        record["views"][0]["derived"]["regions"][0]["embedding"][0] = float("nan")
    with pytest.raises(VisualFailure):
        catalog_object(obj, sources, record)


def test_public_urls_cannot_contain_credentials_or_script_schemes():
    for url in (
        "javascript:alert(1)",
        "https://user:password@example.org",
        "//example.org",
    ):
        assert public_url(url) is None
    assert public_url("https://example.org/source") == "https://example.org/source"
