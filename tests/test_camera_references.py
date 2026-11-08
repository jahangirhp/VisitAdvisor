"""Atlas freshness, isolation, cache recovery and native reference conversion."""

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.modules.live_camera.inference import (
    MAX_CAMERA_OBJECTS,
    CameraFailure,
    CameraRecognizer,
)
from app.modules.live_camera.references import AtlasCameraReferences
from app.modules.live_camera.server import create_camera_app
from tests.test_camera_server import jpeg
from tests.test_live_camera import export_fixture


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return self

    def skip(self, offset):
        self.rows = self.rows[offset:]
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, length):
        return self.rows[:length]


class Collection:
    def __init__(self, rows):
        self.rows = rows
        self.reads = []

    def matching(self, query, projection):
        self.reads.append((query, projection))
        rows = []
        for row in self.rows:
            if not all(
                row.get(key) in value["$in"]
                if isinstance(value, dict)
                else row.get(key) == value
                for key, value in query.items()
            ):
                continue
            rows.append(
                deepcopy(
                    row
                    if projection is None
                    else {
                        key: value
                        for key, value in row.items()
                        if key == "_id" or projection.get(key)
                    }
                )
            )
            if projection and any(key.startswith("views.") for key in projection):
                rows[-1]["views"] = []
                for view in row["views"]:
                    selected = {}
                    for section in ("reference", "derived"):
                        selected[section] = {
                            name: deepcopy(value)
                            for name, value in view.get(section, {}).items()
                            if projection.get(f"views.{section}.{name}")
                        }
                    if "image_hash" in view and projection.get("views.image_hash"):
                        selected["image_hash"] = view["image_hash"]
                    rows[-1]["views"].append(selected)
        return rows

    async def find_one(self, query, projection=None):
        rows = self.matching(query, projection)
        return rows[0] if rows else None

    def find(self, query, projection=None):
        rows = self.matching(query, projection)
        return Cursor(rows)


@pytest.fixture
def atlas(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    obj, sources, record = export_fixture()
    location = obj["location_id"]
    model = {
        "source_model": "test-model",
        "reference_preprocessing": "test-preprocessing",
        "dimensions": 384,
        "input_size": 224,
        "sha256": hashlib.sha256(b"model").hexdigest(),
    }
    record.update(
        model=model["source_model"], preprocessing=model["reference_preprocessing"]
    )
    record["views"][0]["derived"]["local"] = {
        "points": [[i, i] for i in range(8)],
        "descriptors": [[0.0] * 128 for _ in range(8)],
        "size": [224, 224],
    }
    second = deepcopy(obj)
    second["_id"] = ObjectId()
    second["name"] = "Second painting"
    second_record = deepcopy(record)
    second_record["object_id"] = str(second["_id"])
    from app.modules.visual.repository import evidence_hash

    second_record["evidence_hash"] = evidence_hash(second, sources)
    db = {
        name: Collection(rows)
        for name, rows in {
            "locations": [
                {"_id": ObjectId(location), "name": "Museum", "status": "active"}
            ],
            "objects": [obj, second],
            "sources": sources,
            "visual_references": [record, second_record],
        }.items()
    }
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://localhost",
        live_camera_location_id=location,
        live_camera_bundle=tmp_path,
        live_camera_object_ids=[str(obj["_id"]), str(second["_id"])],
    )
    (tmp_path / "model-metadata.json").write_text(json.dumps(model))
    (tmp_path / "model.onnx").write_bytes(b"model")
    sessions = []

    class Session:
        def __init__(self, *args, **kwargs):
            sessions.append(self)

        def run(self, *args):
            return [np.asarray([[1.0] + [0.0] * 383], dtype=np.float32)]

    import app.modules.live_camera.inference as inference

    original_import = inference.importlib.import_module
    monkeypatch.setattr(
        inference.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(SessionOptions=SimpleNamespace, InferenceSession=Session)
            if name == "onnxruntime"
            else original_import(name)
        ),
    )
    return AtlasCameraReferences(db, settings, tmp_path), db, sessions, settings


def test_geometry_experiment_is_opt_in_and_changes_reference_version(atlas):
    loader, db, _, settings = atlas

    async def run():
        baseline = await loader.get()
        assert not baseline.geometry_rerank
        experimental = AtlasCameraReferences(
            db,
            settings.model_copy(update={"live_camera_geometry_rerank": True}),
            loader.directory,
        )
        updated = await experimental.get()
        assert updated.geometry_rerank
        assert updated.reference_version != baseline.reference_version
        assert updated.public_catalog()["policy"]["geometry_ranking"] == (
            "spatial-verification-v1"
        )

    asyncio.run(run())


def test_failed_descriptor_read_cancels_other_reads_and_publishes_no_snapshot(atlas):
    loader, db, _, _ = atlas

    async def run():
        entered = asyncio.Event()
        cancelled = []

        async def read(query, projection):
            if query["object_id"] == loader.object_ids[0]:
                await entered.wait()
                raise RuntimeError("private connection details")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(query["object_id"])

        db["visual_references"].find_one = read
        with pytest.raises(CameraFailure, match="^REFERENCES_UNAVAILABLE$"):
            await loader.get()
        assert cancelled == [loader.object_ids[1]]
        assert loader.recognizer is None
        assert loader.checked_until == 0

    asyncio.run(run())


def test_cache_refresh_changes_metadata_but_reuses_model(atlas):
    loader, db, sessions, _ = atlas

    async def run():
        first = await loader.get()
        reads = sum(len(c.reads) for c in db.values())
        assert await loader.get() is first
        assert sum(len(c.reads) for c in db.values()) == reads
        loader.checked_until = 0
        assert await loader.get() is first
        full_reads = sum(
            bool(p and "views.derived.regions" in p)
            for _, p in db["visual_references"].reads
        )
        assert full_reads == 2
        db["objects"].rows[0]["description"] = "Updated canonical description"
        loader.checked_until = 0
        second = await loader.get()
        assert second.reference_version != first.reference_version
        assert second.session is first.session
        assert len(sessions) == 1
        assert any(
            o.get("description") == "Updated canonical description"
            for o in second.public_catalog()["objects"]
        )
        public = json.dumps(second.public_catalog())
        for private in ("vectors", "local", "raw_record", "prepared_content", "model"):
            assert f'"{private}"' not in public

    asyncio.run(run())


@pytest.mark.parametrize(
    "change", ["source", "moved", "deleted", "inactive", "model", "preprocessing"]
)
def test_changed_or_invalid_evidence_cannot_use_cached_match(atlas, change):
    loader, db, _, _ = atlas

    async def run():
        await loader.get()
        loader.checked_until = 0
        if change == "source":
            db["sources"].rows[0]["raw_record"] = {"changed": True}
        elif change == "moved":
            db["objects"].rows[0]["location_id"] = str(ObjectId())
        elif change == "deleted":
            db["visual_references"].rows.pop()
        elif change == "inactive":
            db["locations"].rows[0]["status"] = "inactive"
        else:
            db["visual_references"].rows[0][change] = "incompatible"
        with pytest.raises(CameraFailure, match="REFERENCES_UNAVAILABLE"):
            await loader.get()
        assert loader.recognizer is None

    asyncio.run(run())


def test_outage_blocks_stale_cache_and_recovery_reuses_session(atlas, monkeypatch):
    loader, db, sessions, _ = atlas

    async def run():
        first = await loader.get()
        loader.checked_until = 0
        original = db["locations"].find_one
        monkeypatch.setattr(
            db["locations"],
            "find_one",
            AsyncMock(side_effect=RuntimeError("mongodb://private:secret@host")),
        )
        with pytest.raises(CameraFailure, match="^REFERENCES_UNAVAILABLE$"):
            await loader.get()
        monkeypatch.setattr(db["locations"], "find_one", original)
        loader.retry_after = 0
        recovered = await loader.get()
        assert recovered.reference_version == first.reference_version
        assert len(sessions) == 1

    asyncio.run(run())


def test_reference_change_during_load_is_not_published(atlas, monkeypatch):
    loader, db, _, _ = atlas
    original = loader.snapshot
    calls = 0

    async def changing():
        nonlocal calls
        calls += 1
        if calls == 2:
            db["visual_references"].rows[0]["set_id"] = "replacement"
        return await original()

    monkeypatch.setattr(loader, "snapshot", changing)
    with pytest.raises(CameraFailure):
        asyncio.run(loader.get())
    assert loader.recognizer is None


def test_refresh_is_serialized_and_reference_version_change_reloads(atlas):
    loader, db, sessions, _ = atlas

    async def run():
        results = await asyncio.gather(*[loader.get() for _ in range(5)])
        assert all(result is results[0] for result in results)
        db["visual_references"].rows[0]["set_id"] = "replacement"
        loader.checked_until = 0
        updated = await loader.get()
        assert updated.reference_version != results[0].reference_version
        assert len(sessions) == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["geometry", "provenance", "vector", "hash"])
def test_invalid_reference_or_model_fails_closed(atlas, change):
    loader, db, _, _ = atlas
    view = db["visual_references"].rows[0]["views"][0]
    if change == "geometry":
        del view["derived"]["local"]
    elif change == "provenance":
        view["reference"]["source_ids"] = [str(ObjectId())]
    elif change == "vector":
        view["derived"]["regions"][0]["embedding"][0] = float("nan")
    else:
        (loader.directory / "model.onnx").write_bytes(b"changed")
    with pytest.raises(CameraFailure):
        asyncio.run(loader.get())


def test_file_and_atlas_inputs_preserve_recognition_scores(atlas, monkeypatch):
    loader, _, _, _ = atlas
    engine = asyncio.run(loader.get())
    (loader.directory / "catalog.json").write_text(json.dumps(engine.catalog))
    replay = CameraRecognizer(loader.directory)
    monkeypatch.setattr(
        "app.modules.live_camera.inference.geometric_score", lambda *args: 0.5
    )
    monkeypatch.setattr(
        "app.modules.live_camera.inference.describe_local",
        lambda *args: {"points": [], "descriptors": [], "size": [224, 224]},
    )
    assert (
        engine.recognize(jpeg())["candidates"] == replay.recognize(jpeg())["candidates"]
    )
    assert engine.geometry_enabled


@pytest.mark.parametrize("explicit_settings", [False, True])
def test_default_server_reads_atlas_without_a_catalog_file(
    atlas, monkeypatch, explicit_settings
):
    loader, db, _, settings = atlas
    connection = SimpleNamespace(database=db, close=AsyncMock())
    monkeypatch.setenv("LIVE_CAMERA_BUNDLE", str(loader.directory))

    def load_settings():
        assert not explicit_settings, "Explicit preview must not reload .env settings"
        return settings

    monkeypatch.setattr("app.modules.live_camera.server.Settings", load_settings)
    monkeypatch.setattr("app.modules.live_camera.server.MongoDB", lambda _: connection)
    with TestClient(
        create_camera_app(configured_settings=settings if explicit_settings else None)
    ) as client:
        response = client.get("/bundle/catalog.json")
        assert response.status_code == 200
        public = response.json()
        assert public["atlas_freshness_verified"] is True
        assert not (loader.directory / "catalog.json").exists()
        assert client.get("/bundle/model-metadata.json").status_code == 404
        assert client.get("/bundle/model.onnx").status_code == 404
        url = (
            f"/recognize?location_id={public['location']['id']}"
            f"&reference_version={public['reference_version']}"
        )
        assert (
            client.post(
                url, content=jpeg(), headers={"Content-Type": "image/jpeg"}
            ).status_code
            == 200
        )
    connection.close.assert_awaited_once()


def test_api_discards_results_when_references_change_during_inference(
    atlas, monkeypatch
):
    loader, db, _, _ = atlas
    app = create_camera_app()
    app.state.references = loader
    with TestClient(app) as client:
        public = client.get("/bundle/catalog.json").json()

        def changed(data):
            db["objects"].rows[0]["name"] = "Changed during inference"
            loader.checked_until = 0
            return {"candidates": [{"id": "stale", "score": 1}]}

        monkeypatch.setattr(loader.recognizer, "recognize", changed)
        url = (
            f"/recognize?location_id={public['location']['id']}"
            f"&reference_version={public['reference_version']}"
        )
        response = client.post(
            url, content=jpeg(), headers={"Content-Type": "image/jpeg"}
        )
        assert response.status_code == 409
        assert response.json() == {"error": "REFERENCES_CHANGED"}


def test_api_outage_is_sanitized_after_freshness_lease(atlas, monkeypatch):
    loader, db, _, _ = atlas
    app = create_camera_app()
    app.state.references = loader
    with TestClient(app) as client:
        assert client.get("/bundle/catalog.json").status_code == 200
        loader.checked_until = 0
        monkeypatch.setattr(
            db["locations"],
            "find_one",
            AsyncMock(side_effect=RuntimeError("mongodb://private:secret@host")),
        )
        response = client.get("/bundle/catalog.json")
        assert response.status_code == 503
        assert response.json() == {"error": "REFERENCES_UNAVAILABLE"}


def test_public_venue_list_includes_new_venues_and_paginates(atlas):
    loader, db, _, settings = atlas
    new_id = ObjectId()
    db["locations"].rows.append(
        {"_id": new_id, "name": "New test venue", "status": "active"}
    )
    db["locations"].rows.append(
        {"_id": ObjectId(), "name": "Archived venue", "status": "archived"}
    )
    app = create_camera_app(configured_settings=settings)
    app.state.mongodb = SimpleNamespace(database=db)
    with TestClient(app) as client:
        response = client.get("/venues")
        assert response.status_code == 200
        assert {row["name"] for row in response.json()} == {"Museum", "New test venue"}
        assert client.get("/venues?offset=1").json() == response.json()[1:]
        assert client.get("/venues?offset=-1").status_code == 422
        result = client.get(f"/bundle/catalog.json?location_id={new_id}")
        assert result.status_code == 503
        assert result.json() == {"error": "NO_PREPARED_REFERENCES"}
        assert loader.recognizer is None


def test_dynamic_selection_is_scoped_supports_one_object_and_refreshes(atlas):
    loader, db, _, settings = atlas
    new_location = str(ObjectId())
    db["locations"].rows.append(
        {"_id": ObjectId(new_location), "name": "New venue", "status": "active"}
    )
    obj = deepcopy(db["objects"].rows[0])
    obj["_id"] = ObjectId()
    obj["location_id"] = new_location
    obj["name"] = "New venue object"
    source = deepcopy(db["sources"].rows[0])
    source["_id"] = ObjectId()
    source["location_id"] = new_location
    obj["source_ids"] = [str(source["_id"])]
    record = deepcopy(db["visual_references"].rows[0])
    record.update(location_id=new_location, object_id=str(obj["_id"]))
    record["views"][0]["reference"]["source_ids"] = obj["source_ids"]
    from app.modules.visual.repository import evidence_hash

    record["evidence_hash"] = evidence_hash(obj, [source])
    db["objects"].rows.append(obj)
    db["sources"].rows.append(source)
    db["visual_references"].rows.append(record)
    app = create_camera_app(configured_settings=settings)
    app.state.references = loader
    app.state.mongodb = SimpleNamespace(database=db)
    with TestClient(app) as client:
        first = client.get("/bundle/catalog.json").json()
        response = client.get(f"/bundle/catalog.json?location_id={new_location}")
        assert response.status_code == 200, response.text
        selected = response.json()
        assert selected["location"]["id"] == new_location
        assert [o["id"] for o in selected["objects"]] == [str(obj["_id"])]
        wrong = client.post(
            f"/recognize?location_id={new_location}&reference_version={first['reference_version']}",
            content=jpeg(),
            headers={"Content-Type": "image/jpeg"},
        )
        assert wrong.status_code == 409
        assert (
            client.get("/bundle/catalog.json").json()["location"] == first["location"]
        )
        db["visual_references"].rows.remove(record)
        app.state.venue_references.checked_until = 0
        assert client.get(
            f"/bundle/catalog.json?location_id={new_location}"
        ).json() == {"error": "NO_PREPARED_REFERENCES"}


def test_dynamic_selection_rejects_unknown_and_invalid_venues(atlas):
    loader, db, _, settings = atlas
    app = create_camera_app(configured_settings=settings)
    app.state.references = loader
    app.state.mongodb = SimpleNamespace(database=db)
    with TestClient(app) as client:
        for location in ["invalid", str(ObjectId())]:
            assert (
                client.get(f"/bundle/catalog.json?location_id={location}").status_code
                == 409
            )


def test_dynamic_selection_does_not_silently_truncate_objects(atlas):
    loader, db, _, settings = atlas
    db["visual_references"].rows.extend(
        {"location_id": settings.live_camera_location_id, "object_id": str(ObjectId())}
        for _ in range(MAX_CAMERA_OBJECTS - 1)
    )
    dynamic = AtlasCameraReferences(
        db, settings, loader.directory, discover_objects=True
    )
    with pytest.raises(CameraFailure) as failure:
        asyncio.run(dynamic.get())
    assert failure.value.code == "CAMERA_OBJECT_LIMIT"


def add_prepared_object(db, name):
    from app.modules.visual.repository import evidence_hash

    obj = deepcopy(db["objects"].rows[0])
    obj["_id"] = ObjectId()
    obj["name"] = name
    source = deepcopy(db["sources"].rows[0])
    source["_id"] = ObjectId()
    obj["source_ids"] = [str(source["_id"])]
    record = deepcopy(db["visual_references"].rows[0])
    record.update(object_id=str(obj["_id"]), evidence_hash=evidence_hash(obj, [source]))
    record["views"][0]["reference"]["source_ids"] = obj["source_ids"]
    db["objects"].rows.append(obj)
    db["sources"].rows.append(source)
    db["visual_references"].rows.append(record)
    return str(obj["_id"]), record


def test_default_venue_discovers_new_prepared_objects_and_reuses_model(atlas):
    loader, db, sessions, settings = atlas
    for index in range(13):
        add_prepared_object(db, f"Prepared object {index}")
    app = create_camera_app(configured_settings=settings)
    app.state.mongodb = SimpleNamespace(database=db)
    with TestClient(app) as client:
        first = client.get("/bundle/catalog.json")
        assert first.status_code == 200, first.text
        assert len(first.json()["objects"]) == 15
        santa_id, _ = add_prepared_object(db, "Santa train")
        app.state.references.checked_until = 0
        updated = client.get("/bundle/catalog.json").json()
        assert len(updated["objects"]) == 16
        assert santa_id in {obj["id"] for obj in updated["objects"]}
        assert updated["reference_version"] != first.json()["reference_version"]
        assert len(sessions) == 1


def test_discovery_excludes_stale_incompatible_moved_and_missing_sources(atlas):
    loader, db, _, settings = atlas
    _, stale = add_prepared_object(db, "Stale")
    stale["evidence_hash"] = "old"
    _, incompatible = add_prepared_object(db, "Other model")
    incompatible["model"] = "incompatible"
    add_prepared_object(db, "Moved")
    db["objects"].rows[-1]["location_id"] = str(ObjectId())
    add_prepared_object(db, "Missing evidence")
    db["sources"].rows.pop()
    dynamic = AtlasCameraReferences(
        db, settings, loader.directory, discover_objects=True
    )
    result = asyncio.run(dynamic.get())
    assert {obj["id"] for obj in result.catalog["objects"]} == set(
        settings.live_camera_object_ids
    )


def test_legacy_sift_ties_are_bounded_without_changing_stored_references(atlas):
    loader, db, _, settings = atlas
    local = db["visual_references"].rows[0]["views"][0]["derived"]["local"]
    local["points"] = [[1, 1]] * 801
    local["descriptors"] = [[0.0] * 128] * 801
    dynamic = AtlasCameraReferences(
        db, settings, loader.directory, discover_objects=True
    )
    result = asyncio.run(dynamic.get())
    assert (
        len(
            result.local_views[db["visual_references"].rows[0]["object_id"]][0][
                "points"
            ]
        )
        == 800
    )
    assert len(local["points"]) == 801
    assert len(local["descriptors"]) == 801


def test_prepared_3d_object_does_not_need_planar_keypoints(atlas):
    from app.modules.visual.repository import evidence_hash

    loader, db, _, settings = atlas
    obj = db["objects"].rows[0]
    obj["type"] = "sculpture"
    record = db["visual_references"].rows[0]
    record["evidence_hash"] = evidence_hash(obj, db["sources"].rows)
    for view in record["views"]:
        del view["derived"]["local"]
    dynamic = AtlasCameraReferences(
        db, settings, loader.directory, discover_objects=True
    )
    result = asyncio.run(dynamic.get())
    assert result.local_views[str(obj["_id"])] == []
    assert result.catalog["policy"]["geometry_scope"] == "planar-and-scene-v2"
    stored = next(o for o in result.catalog["objects"] if o["id"] == str(obj["_id"]))
    assert all("local" not in view for view in stored["views"])


def test_selected_object_references_exclude_legacy_scene_fallbacks(atlas):
    loader, db, _, _ = atlas
    record = db["visual_references"].rows[0]
    focused = record["views"][0]
    focused["image_hash"] = "a" * 64
    focused["derived"]["preparation"] = {
        "version": "object-focus-v1",
        "source_sha256": focused["image_hash"],
    }
    fallback = deepcopy(focused)
    fallback["image_hash"] = "b" * 64
    del fallback["derived"]["preparation"]
    record["views"].append(fallback)
    engine = asyncio.run(loader.get())
    target = next(
        o for o in engine.catalog["objects"] if o["id"] == record["object_id"]
    )
    assert len(target["views"]) == 1
    assert target["views"][0]["image_hash"] == "a" * 64
    # The source record and its fallback remain intact in Atlas.
    assert len(record["views"]) == 2
    assert "preparation" not in json.dumps(engine.public_catalog())


def test_focused_reference_with_mismatched_source_is_rejected(atlas):
    loader, db, _, _ = atlas
    view = db["visual_references"].rows[0]["views"][0]
    view["image_hash"] = "a" * 64
    view["derived"]["preparation"] = {
        "version": "object-focus-v1",
        "source_sha256": "b" * 64,
    }
    with pytest.raises(CameraFailure, match="REFERENCES_UNAVAILABLE"):
        asyncio.run(loader.get())


def test_admin_invalidation_drops_deleted_object_before_freshness_lease_ends(atlas):
    loader, db, sessions, settings = atlas
    loader = AtlasCameraReferences(
        db, settings, loader.directory, discover_objects=True
    )

    async def run():
        first = await loader.get()
        removed = loader.object_ids[0]
        db["objects"].rows = [
            row for row in db["objects"].rows if str(row["_id"]) != removed
        ]
        db["visual_references"].rows = [
            row for row in db["visual_references"].rows if row["object_id"] != removed
        ]
        # The ordinary lease is still fresh, so explicit invalidation matters.
        assert await loader.get() is first
        await loader.invalidate()
        assert loader.recognizer is None
        assert loader.retry_after == loader.checked_until == 0
        assert loader.session is first.session
        current = await loader.get()
        assert current.reference_version != first.reference_version
        assert all(obj["id"] != removed for obj in current.catalog["objects"])
        assert current.session is first.session
        assert len(sessions) == 1

    asyncio.run(run())
