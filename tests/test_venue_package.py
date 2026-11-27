import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

np = pytest.importorskip("numpy")

from app.modules.live_camera.inference import CameraRecognizer  # noqa: E402
from app.modules.live_camera.pipeline import PIPELINE_VERSION  # noqa: E402
from app.modules.live_camera.venue_package import (  # noqa: E402
    DIMENSIONS,
    PackageError,
    build_package,
    validate_package,
)


def unit(rng, n):
    m = rng.standard_normal((n, DIMENSIONS)).astype(np.float32)
    return (m / np.linalg.norm(m, axis=1, keepdims=True)).tolist()


def catalog(seed=7):
    rng = np.random.default_rng(seed)
    objects = []
    for index, kind in enumerate(["painting", "sculpture", "print", "vase"]):
        planar = kind in {"painting", "print"}
        views = []
        for side in range(1 + index % 3):
            view = {
                "vectors": unit(rng, 1 + (index + side) % 5),
                "image_hash": f"image-{index}-{side}",
                "viewpoint": ["front", "left", "back"][side],
                "rights_status": "public-domain",
                "image_url": None,
                "source_ids": ["s"],
            }
            if planar:
                count = 20 + index
                view["local"] = {
                    "points": rng.uniform(0, 200, (count, 2)).tolist(),
                    "descriptors": rng.integers(0, 255, (count, 128))
                    .astype(float)
                    .tolist(),
                    "size": [224, 224],
                }
            views.append(view)
        objects.append(
            {
                "id": f"65{index:022x}",
                "location_id": "loc",
                "name": f"Exhibit {index}",
                "type": kind,
                "creator": "Artist",
                "year": 1890 + index,
                "description": "Source-backed text.",
                "sources": [{"id": "s", "title": "Museum record", "url": None}],
                "views": views,
                "recognition_profile": "planar" if planar else "multiview",
                "reference_view_coverage": {
                    "profile": "planar" if planar else "multiview",
                    "distinct_images": len(views),
                    "distinct_viewpoints": len(views),
                    "ready": True,
                },
            }
        )
    now = datetime.now(UTC)
    return {
        "version": "live-camera-v1",
        "reference_version": "f" * 64,
        "reference_signature": "e" * 64,
        "location": {"id": "loc", "name": "Test museum"},
        "objects": objects,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "automatic_identification": False,
        "model": {
            "dimensions": 384,
            "input_size": 224,
            "sha256": "b" * 64,
            "source_model": "facebook/dinov2-small@"
            "ed25f3a31f01632728cabb09d1542f84ab7b0056",
        },
        "policy": {
            "global_weight": 0.75,
            "calibrated": False,
            "candidate_floor": 0.55,
            "candidate_margin": 0.04,
            "stable_frames": 3,
            "geometry_min_score": 0.4,
            "recognition_pipeline": PIPELINE_VERSION,
            "object_regions": True,
            "inference_budget_ms": 1500,
            "minimum_stable_ms": 600,
        },
    }


def test_package_round_trips_and_validates(tmp_path):
    manifest = build_package(catalog(), tmp_path / "pkg")
    assert validate_package(tmp_path / "pkg") == manifest
    assert manifest["exhibitCount"] == 4
    rows = manifest["embeddingSpaces"][0]["rowCount"]
    expected = sum(len(v["vectors"]) for o in catalog()["objects"] for v in o["views"])
    assert rows == expected
    assert not list((tmp_path).glob(".stage-*"))


def test_package_is_deterministic_and_immutable(tmp_path):
    first = build_package(catalog(), tmp_path / "a")
    second = build_package(catalog(), tmp_path / "b")
    assert first["files"] == second["files"]
    # Rebuilding the same version is idempotent; different content is refused.
    assert build_package(catalog(), tmp_path / "a")["files"] == first["files"]
    changed = catalog(seed=8)
    with pytest.raises(PackageError, match="VERSION_CONTENT_CONFLICT"):
        build_package(changed, tmp_path / "a")


def test_matrix_rows_and_mapping_preserve_vectors_exactly(tmp_path):
    source = catalog()
    manifest = build_package(source, tmp_path / "pkg")
    space = manifest["embeddingSpaces"][0]
    matrix = np.fromfile(tmp_path / "pkg" / space["matrixPath"], dtype="<f4").reshape(
        space["rowCount"], space["dimension"]
    )
    db = sqlite3.connect(tmp_path / "pkg" / "catalog.sqlite")
    by_id = {o["id"]: o for o in source["objects"]}
    for row, exhibit, view, region in db.execute(
        "SELECT row_index, exhibit_id, view_index, region_index FROM vector_rows"
    ):
        original = np.asarray(
            by_id[exhibit]["views"][view]["vectors"][region], np.float32
        )
        assert np.array_equal(matrix[row], original)


def test_package_scores_match_server_recognizer(tmp_path):
    """Golden parity: the package reproduces the server's per-object view scores."""
    source = catalog()
    manifest = build_package(source, tmp_path / "pkg")
    engine = CameraRecognizer(tmp_path, source, session=object())
    space = manifest["embeddingSpaces"][0]
    matrix = np.fromfile(tmp_path / "pkg" / space["matrixPath"], dtype="<f4").reshape(
        -1, 384
    )
    db = sqlite3.connect(tmp_path / "pkg" / "catalog.sqlite")
    mapping = list(
        db.execute(
            "SELECT exhibit_id, view_index, region_index FROM vector_rows "
            "ORDER BY row_index"
        )
    )
    rng = np.random.default_rng(1)
    for _ in range(20):
        query = rng.standard_normal(384).astype(np.float32)
        query /= np.linalg.norm(query)
        scores = matrix @ query
        package_views: dict[tuple[str, int], list[float]] = {}
        for (exhibit, view, _region), score in zip(mapping, scores, strict=True):
            package_views.setdefault((exhibit, view), []).append(float(score))
        for exhibit, views in engine.views:
            for view_index, view in enumerate(views):
                s = view @ query
                server = 0.75 * float(s[0]) + 0.25 * float(s.max())
                p = package_views[(exhibit, view_index)]
                assert abs((0.75 * p[0] + 0.25 * max(p)) - server) < 1e-6


@pytest.mark.parametrize(
    "tamper, code",
    [
        (
            lambda p: p.joinpath("vectors/dinov2-small-cls.f32").write_bytes(b"\0" * 8),
            "SIZE_MISMATCH",
        ),
        (
            lambda p: p.joinpath("decision-policy.json").write_text("{}\n" + " " * 0),
            "SIZE_MISMATCH",
        ),
        (lambda p: p.joinpath("catalog.sqlite").unlink(), "MISSING_FILE"),
    ],
)
def test_tampered_packages_are_rejected(tmp_path, tamper, code):
    build_package(catalog(), tmp_path / "pkg")
    tamper(tmp_path / "pkg")
    with pytest.raises(PackageError, match=code):
        validate_package(tmp_path / "pkg")


def test_same_size_corruption_fails_checksum(tmp_path):
    build_package(catalog(), tmp_path / "pkg")
    path = tmp_path / "pkg" / "vectors/dinov2-small-cls.f32"
    data = bytearray(path.read_bytes())
    data[100] ^= 1
    path.write_bytes(bytes(data))
    with pytest.raises(PackageError, match="CHECKSUM_MISMATCH"):
        validate_package(tmp_path / "pkg")


def test_manifest_path_traversal_and_placeholders_rejected(tmp_path):
    build_package(catalog(), tmp_path / "pkg")
    manifest_path = tmp_path / "pkg" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PackageError, match="UNSAFE_PATH"):
        validate_package(tmp_path / "pkg")
    manifest["files"][0]["path"] = "catalog.sqlite"
    manifest["venueID"] = "EXAMPLE_VENUE"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(PackageError, match="PLACEHOLDER_VALUE"):
        validate_package(tmp_path / "pkg")


def test_invalid_catalogs_are_refused(tmp_path):
    zero = catalog()
    zero["objects"][1]["views"][0]["vectors"][0] = [0.0] * 384
    with pytest.raises(PackageError, match="ZERO_NORM_VECTOR"):
        build_package(zero, tmp_path / "a")
    missing = catalog()
    del missing["objects"][0]["views"][0]["local"]
    with pytest.raises(PackageError, match="PLANAR_LOCAL_FEATURES_MISSING"):
        build_package(missing, tmp_path / "b")
    automatic = catalog()
    automatic["automatic_identification"] = True
    with pytest.raises(PackageError, match="UNSUPPORTED_CATALOG"):
        build_package(automatic, tmp_path / "c")
    assert not list(tmp_path.glob(".stage-*"))


def test_package_routes_serve_only_manifest_listed_assets(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.modules.live_camera.server import create_camera_app

    source = catalog()
    source["location"]["id"] = "65" + "0" * 22
    for obj in source["objects"]:
        obj["location_id"] = source["location"]["id"]
    engine = CameraRecognizer(tmp_path, source, session=object())
    settings = Settings(
        mongodb_uri="mongodb://localhost:1/test",
        live_camera_package_cache=tmp_path / "cache",
        live_camera_offline_packages=True,
    )
    app = create_camera_app(recognizer=engine, configured_settings=settings)
    location = source["location"]["id"]
    with TestClient(app) as client:
        response = client.get(f"/venues/{location}/package/manifest")
        assert response.status_code == 200
        manifest = response.json()
        etag = response.headers["etag"]
        assert etag == f'"{manifest["packageVersion"]}"'
        again = client.get(
            f"/venues/{location}/package/manifest", headers={"If-None-Match": etag}
        )
        assert again.status_code == 304
        base = f"/venues/{location}/package/{manifest['packageVersion']}"
        for entry in manifest["files"]:
            body = client.get(f"{base}/{entry['path']}").content
            assert len(body) == entry["sizeBytes"]
        assert client.get(f"{base}/../../etc/passwd").status_code == 404
        assert client.get(f"{base}/vectors").status_code == 404
        assert client.get(f"/venues/{'1' * 24}/package/manifest").status_code == 409
        assert client.get("/venues/not-an-id/package/manifest").status_code == 422


def test_package_routes_are_disabled_by_default(tmp_path):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.modules.live_camera.server import create_camera_app

    engine = CameraRecognizer(tmp_path, catalog(), session=object())
    settings = Settings(mongodb_uri="mongodb://localhost:1/test")
    with TestClient(
        create_camera_app(recognizer=engine, configured_settings=settings)
    ) as client:
        assert client.get(f"/venues/{'1' * 24}/package/manifest").status_code == 404
        assert (
            client.get(
                f"/venues/{'1' * 24}/package/{'f' * 64}/manifest.json"
            ).status_code
            == 404
        )
