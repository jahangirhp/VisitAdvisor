"""Visual recognition invariants, with no museum, model download, or paid calls."""

import asyncio
import io
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pymongo.errors import OperationFailure

from app.core.exceptions import ApplicationError
from app.main import create_app
from app.modules.recognition.router import get_service
from app.modules.visual.benchmark import (
    BenchmarkPolicy,
    Photo,
    Thresholds,
    benchmark,
    select,
    validate_manifest,
)
from app.modules.visual.features import (
    REGIONAL_GLOBAL_WEIGHT,
    VisualFailure,
    geometric_score,
    rank,
    unit_vector,
)
from app.modules.visual.preparation import ManualReference, prepare
from app.modules.visual.references import ReferenceClient, validate_url
from app.modules.visual.repository import (
    RUNTIME_FIELDS,
    VisualRepository,
    evidence_snapshot,
)
from app.modules.visual.service import VisualService
from tests.test_domain import LOCATION
from tests.test_retrieval import L1, L2, NOW, S1, RetrievalCollection, object_doc

IMAGE_URL = "https://iiif.micr.io/example/full/800,/0/default.jpg"


def picture():
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 50, 80), fill="blue")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def features(vector=None):
    vector = vector or [1.0] + [0.0] * 383
    return {
        "regions": [{"box": [0, 0, 1, 1], "embedding": vector}],
        "local": {"points": [], "descriptors": [], "size": [100, 100]},
        "size": [100, 100],
    }


class VisualCollection(RetrievalCollection):
    async def list_search_indexes(self, name):
        return SimpleNamespace(to_list=AsyncMock(return_value=[]))

    async def aggregate(self, pipeline, **kwargs):
        # Small in-memory join fixture. The real aggregation is separately exercised
        # against Atlas; mutations in behavioral tests remain visible on each read.
        location = pipeline[0]["$match"]["location_id"]
        version = pipeline[5]["$lookup"]["pipeline"][0]["$match"]
        rows = []
        for obj in self.documents:
            if obj["location_id"] != location:
                continue
            row = deepcopy(obj)
            sources = [
                deepcopy(s)
                for s in self.db["sources"].documents
                if str(s["_id"]) in obj.get("source_ids", [])
                and s["location_id"] == location
            ]
            row["_visual_references"] = [
                {key: deepcopy(r[key]) for key in RUNTIME_FIELDS}
                for r in self.db["visual_references"].documents
                if r["object_id"] == str(obj["_id"])
                and all(
                    r[k] == version[k]
                    for k in ("location_id", "model", "preprocessing")
                )
                and r.get("views")
                and r.get("vector_count", 0) > 0
                and r.get("canonical_evidence") == evidence_snapshot(obj, sources)
            ]
            rows.append(row)
        return SimpleNamespace(to_list=AsyncMock(return_value=rows))

    def find(self, query, projection=None):
        cursor = super().find(query, projection)
        cursor.batch_size = lambda size: cursor
        return cursor

    async def bulk_write(self, operations):
        for operation in operations:
            await self.replace_one(operation._filter, operation._doc, operation._upsert)

    async def delete_many(self, query):
        self.documents = [doc for doc in self.documents if not self.matches(doc, query)]

    async def replace_one(self, query, doc, upsert=False):
        previous = next((d for d in self.documents if self.matches(d, query)), None)
        if previous is not None:
            self.documents.remove(previous)
            self.documents.append({"_id": previous["_id"], **deepcopy(doc)})
        elif upsert:
            self.documents.append({"_id": ObjectId(), **deepcopy(doc)})
        return SimpleNamespace(matched_count=int(previous is not None))


@pytest.fixture
def setup():
    db = {
        name: VisualCollection()
        for name in (
            "locations",
            "objects",
            "sources",
            "visual_references",
            "visual_vectors",
        )
    }
    db["locations"].documents = [
        {**LOCATION, "_id": ObjectId(L1), "created_at": NOW, "updated_at": NOW}
    ]
    for collection in db.values():
        collection.db = db
    db["objects"].documents = [object_doc()]
    db["sources"].documents = [
        {
            "_id": ObjectId(S1),
            "location_id": L1,
            "raw_record": {"title": "source evidence"},
        }
    ]
    encoder = SimpleNamespace(
        model="test-model", preprocessing="v1", describe=Mock(return_value=features())
    )
    return db, VisualRepository(db), encoder


def manual_ref(db, **changes):
    return ManualReference(
        **{
            "object_id": str(db["objects"].documents[0]["_id"]),
            "source_ids": [S1],
            "image_url": IMAGE_URL,
            "viewpoint": "front",
            "rights_status": "unknown",
            "evidence": {"note": "operator-supplied source-linked reference"},
            **changes,
        }
    )


async def prepare_one(setup, manual=None, handler=None):
    db, repo, encoder = setup
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            handler
            or (
                lambda _: httpx.Response(
                    200, content=picture(), headers={"content-type": "image/png"}
                )
            )
        )
    ) as http:
        return await prepare(
            repo,
            L1,
            encoder,
            http,
            25,
            manual=manual if manual is not None else [manual_ref(db)],
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://iiif.micr.io/image",
        "https://localhost/image",
        "https://127.0.0.1/",
        "https://iiif.micr.io.evil.test/image",
        "https://thumb.wikimedia.org.evil.test/image",
        "https://user:pass@iiif.micr.io/image",
        "https://iiif.micr.io:444/image",
        "file:///etc/passwd",
        "https://iiif.micr.io:bad/image",
    ],
)
def test_reference_url_allowlist(url):
    with pytest.raises(VisualFailure, match="UNEXPECTED_REFERENCE_URL"):
        validate_url(url)


def test_wikimedia_thumbnail_reference_download():
    url = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/test.jpg/1280px-test.jpg"

    def handler(request):
        assert str(request.url) == url
        return httpx.Response(
            200, content=picture(), headers={"content-type": "image/png"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ReferenceClient(http).image(url)

    clean, image_hash = asyncio.run(run())
    assert clean.startswith(b"\xff\xd8")
    assert len(image_hash) == 64


def test_redirect_is_validated_before_following():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(VisualFailure, match="UNEXPECTED_REFERENCE_URL"):
                await ReferenceClient(http).image(IMAGE_URL)

    asyncio.run(run())
    assert calls == [IMAGE_URL]


def test_reference_download_identifies_application_on_redirects():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                302, headers={"location": "https://upload.wikimedia.org/image.jpg"}
            )
        # Wikimedia rejects the generic httpx user agent on real downloads.
        if not request.headers.get("user-agent", "").startswith("VisitAdvisor/"):
            return httpx.Response(403)
        return httpx.Response(
            200, content=b"image", headers={"content-type": "image/jpeg"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ReferenceClient(http).fetch(IMAGE_URL, 20)

    assert asyncio.run(run()) == (b"image", "image/jpeg")
    assert len(calls) == 2
    assert all(r.headers["user-agent"].startswith("VisitAdvisor/") for r in calls)


def test_download_budget_and_byte_limit():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"0123456789")
            )
        ) as http:
            client = ReferenceClient(http)
            with pytest.raises(VisualFailure, match="REFERENCE_TOO_LARGE"):
                await client.fetch(IMAGE_URL, 5)
            client.requests = 32
            with pytest.raises(VisualFailure, match="REFERENCE_REQUEST_LIMIT"):
                await client.fetch(IMAGE_URL, 20)

    asyncio.run(run())


def test_resolver_keeps_linked_evidence_and_reports_unknown_rights():
    source = {
        "_id": ObjectId(S1),
        "raw_record": {
            "id": "https://id.rijksmuseum.nl/1",
            "shows": [{"id": "https://id.rijksmuseum.nl/2"}],
        },
    }

    def handler(request):
        if request.url.path == "/2":
            return httpx.Response(
                200,
                json={
                    "id": "https://id.rijksmuseum.nl/2",
                    "digitally_shown_by": [{"id": "https://id.rijksmuseum.nl/3"}],
                },
            )
        if request.url.path == "/3":
            return httpx.Response(
                200,
                json={
                    "id": "https://id.rijksmuseum.nl/3",
                    "access_point": [{"id": IMAGE_URL}],
                },
            )
        return httpx.Response(200, json={"type": "Manifest"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await ReferenceClient(http).resolve([source])

    refs, warnings = asyncio.run(run())
    assert refs[0]["source_ids"] == [S1]
    assert refs[0]["evidence"]["visual"]["id"].endswith("/2")
    assert refs[0]["rights_status"] == "unknown"
    assert warnings == ["REFERENCE_RIGHTS_UNKNOWN"]


def test_preparation_is_repeatable_and_preserves_canonical_data(setup):
    db, repo, encoder = setup
    before = deepcopy({key: db[key].documents for key in ("objects", "sources")})
    first = asyncio.run(prepare_one(setup))
    second = asyncio.run(prepare_one(setup))
    assert first["prepared"] == 1 and second["skipped"] == 1
    assert encoder.describe.call_count == 1
    assert len(db["visual_vectors"].documents) == 1
    for key, value in before.items():
        assert db[key].documents == value
    assert asyncio.run(repo.active(L1, encoder.model, encoder.preprocessing))[0]
    assert not asyncio.run(repo.active(L2, encoder.model, encoder.preprocessing))[0]


@pytest.mark.parametrize(
    "change", ["source", "model", "preprocessing", "removed", "moved", "missing_source"]
)
@pytest.mark.parametrize("include_features", [True, False])
def test_stale_or_wrong_location_references_are_excluded(
    setup, change, include_features
):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    if change == "source":
        db["sources"].documents[0]["raw_record"] = {"title": "changed"}
    elif change in ("model", "preprocessing"):
        setattr(encoder, change, "changed")
    elif change == "removed":
        db["objects"].documents.clear()
    elif change == "moved":
        db["objects"].documents[0]["location_id"] = L2
    else:
        db["sources"].documents.clear()
    assert not asyncio.run(
        repo.active(
            L1, encoder.model, encoder.preprocessing, include_features=include_features
        )
    )[0]


def test_failed_refresh_preserves_previous_reference_set(setup):
    db, _, encoder = setup
    asyncio.run(prepare_one(setup))
    before = deepcopy(db["visual_references"].documents)
    encoder.model = "changed"
    encoder.describe.side_effect = VisualFailure("VISUAL_ENCODING_FAILED")
    report = asyncio.run(prepare_one(setup))
    assert report["failed"] == 1
    assert db["visual_references"].documents == before


def test_model_change_rebuilds_reference_and_cleans_old_vectors(setup):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    previous_id = db["visual_references"].documents[0]["set_id"]
    encoder.model = "new-model"
    report = asyncio.run(prepare_one(setup))
    assert report["prepared"] == 1 and encoder.describe.call_count == 2
    assert len(db["visual_vectors"].documents) == 1
    assert db["visual_vectors"].documents[0]["set_id"] != previous_id
    assert asyncio.run(repo.active(L1, encoder.model, encoder.preprocessing))[0]


def test_failed_staging_does_not_publish_and_concurrent_evidence_change_rejects(setup):
    db, _, encoder = setup
    db["visual_vectors"].replace_one = AsyncMock(side_effect=OperationFailure("secret"))
    report = asyncio.run(prepare_one(setup))
    assert report["failed"] == 1 and "secret" not in str(report)
    assert not db["visual_references"].documents
    del db["visual_vectors"].replace_one

    def change_source(*args, **kwargs):
        db["sources"].documents[0]["raw_record"] = {"title": "concurrent edit"}
        return features()

    encoder.describe.side_effect = change_source
    report = asyncio.run(prepare_one(setup))
    assert report["objects"][0]["code"] == "REFERENCE_EVIDENCE_CHANGED"
    assert not db["visual_references"].documents


def test_manual_references_enforce_object_and_source_membership(setup):
    db, _, encoder = setup
    with pytest.raises(VisualFailure, match="MANIFEST_OBJECT_OUTSIDE_SELECTION"):
        asyncio.run(prepare_one(setup, [manual_ref(db, object_id=str(ObjectId()))]))
    report = asyncio.run(
        prepare_one(setup, [manual_ref(db, source_ids=[str(ObjectId())])])
    )
    assert report["objects"][0]["code"] == "REFERENCE_SOURCE_MISMATCH"
    encoder.describe.assert_not_called()


def test_regions_and_multiple_views_rank_distinct_objects():
    q = features([0, 1])
    ref = features([1, 0])
    ref["regions"].append({"box": [0, 0, 0.65, 0.65], "embedding": [0, 1]})
    records = [
        {"object_id": "one", "views": [{"derived": ref}] * 4},
        {"object_id": "two", "views": [{"derived": features([0.6, 0.8])}]},
    ]
    assert rank(q, records, "global")[0]["object_id"] == "two"
    assert rank(q, records, "regional")[0]["object_id"] == "one"
    assert (
        rank(
            q,
            records,
            "regional",
            regional_global_weight=REGIONAL_GLOBAL_WEIGHT,
        )[0]["object_id"]
        == "two"
    )
    assert len(rank(q, records, "regional")) == 2


@pytest.mark.parametrize(
    "vector", [[], [0, 0], [True, 1], [float("nan"), 1], [float("inf"), 1]]
)
def test_invalid_embeddings_are_rejected(vector):
    with pytest.raises(VisualFailure):
        unit_vector(vector, 2)


def test_atlas_query_filters_active_location_model_and_versions(setup):
    db, repo, _ = setup
    db["visual_vectors"].aggregate = AsyncMock(
        return_value=SimpleNamespace(
            to_list=AsyncMock(
                return_value=[
                    {
                        "_id": "active",
                        "object_id": "one",
                        "count": 5,
                        "score": 0.825,
                    }
                ]
            )
        )
    )
    result = asyncio.run(
        repo.search(L1, [1, 0], [{"set_id": "active", "views": [{}]}], "model", "prep")
    )
    pipeline = db["visual_vectors"].aggregate.call_args.args[0]
    query = pipeline[0]["$vectorSearch"]
    assert query["filter"] == {
        "location_id": L1,
        "model": "model",
        "preprocessing": "prep",
        "dimensions": 384,
        "set_id": {"$in": ["active"]},
    }
    assert query["exact"] is True and query["limit"] == 5
    first_group = pipeline[2]["$group"]
    assert first_group["_id"] == {
        "set_id": "$set_id",
        "view_id": "$view_id",
    }
    assert first_group["regional_score"] == {"$max": "$score"}
    assert pipeline[3]["$group"]["count"] == {"$sum": "$count"}
    expected = 2 * 0.825 - 1
    assert result[0]["score"] == pytest.approx(expected)


def test_visual_never_auto_selects_even_with_perfect_score(setup):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    obj = db["objects"].documents[0]
    repo.index_status = AsyncMock(return_value="ready")
    repo.search = AsyncMock(return_value=[{"object_id": str(obj["_id"]), "score": 1.0}])
    service = VisualService(repo, encoder, asyncio.Lock())
    result = asyncio.run(service.recognize(L1, picture(), "image/png"))
    assert result.status == "ambiguous" and result.object is None and result.ocr is None
    assert len(result.candidates) == 1
    assert result.candidates[0].warnings == ["CONTENT_NOT_PREPARED"]
    assert "VISUAL_UNCALIBRATED" in result.warnings


def test_index_lag_cannot_return_incomplete_candidate_rankings(setup):
    db, repo, _ = setup
    db["visual_vectors"].aggregate = AsyncMock(
        return_value=SimpleNamespace(to_list=AsyncMock(return_value=[]))
    )
    with pytest.raises(VisualFailure, match="VISUAL_INDEX_UPDATING"):
        asyncio.run(
            repo.search(
                L1, [1, 0], [{"set_id": "new-set", "views": [{}]}], "model", "prep"
            )
        )


def test_missing_coverage_skips_inference_and_missing_location_is_rejected(setup):
    _, repo, encoder = setup
    service = VisualService(repo, encoder, asyncio.Lock())
    result = asyncio.run(service.recognize(L1, picture(), "image/png"))
    assert (
        result.status == "no_match" and "VISUAL_REFERENCES_MISSING" in result.warnings
    )
    with pytest.raises(ApplicationError):
        asyncio.run(service.recognize(L2, picture(), "image/png"))
    encoder.describe.assert_not_called()


def test_visual_index_failure_does_not_fallback_to_ocr(setup):
    _, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    encoder.describe.reset_mock()
    repo.index_status = AsyncMock(return_value="building")
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            VisualService(repo, encoder, asyncio.Lock()).recognize(
                L1, picture(), "image/png"
            )
        )
    assert error.value.code == "VISUAL_INDEX_NOT_READY"
    encoder.describe.assert_not_called()


def test_concurrent_source_edit_during_search_cannot_return_candidate(setup):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    repo.index_status = AsyncMock(return_value="ready")

    async def changed(*args):
        db["sources"].documents[0]["raw_record"] = {"changed": True}
        return [{"object_id": str(db["objects"].documents[0]["_id"]), "score": 1.0}]

    repo.search = changed
    result = asyncio.run(
        VisualService(repo, encoder, asyncio.Lock()).recognize(
            L1, picture(), "image/png"
        )
    )
    assert result.status == "no_match" and not result.candidates


@pytest.mark.parametrize("change", ["moved", "deleted", "renamed"])
def test_final_visual_snapshot_revalidates_object_changes(setup, change):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    repo.index_status = AsyncMock(return_value="ready")
    obj = db["objects"].documents[0]

    async def changed(*args):
        if change == "moved":
            obj["location_id"] = L2
        elif change == "deleted":
            db["objects"].documents.clear()
        else:
            obj["name"] = "Corrected canonical title"
        return [{"object_id": str(obj["_id"]), "score": 1.0}]

    repo.search = changed
    result = asyncio.run(
        VisualService(repo, encoder, asyncio.Lock()).recognize(
            L1, picture(), "image/png"
        )
    )
    if change == "renamed":
        assert result.candidates[0].name == "Corrected canonical title"
    else:
        assert result.status == "no_match" and not result.candidates


def test_index_mismatch_is_not_replaced(setup):
    db, repo, _ = setup
    db["visual_vectors"].list_search_indexes = AsyncMock(
        return_value=SimpleNamespace(
            to_list=AsyncMock(
                return_value=[
                    {"type": "vectorSearch", "latestDefinition": {"fields": []}}
                ]
            )
        )
    )
    db["visual_vectors"].create_search_index = AsyncMock()
    with pytest.raises(VisualFailure, match="VISUAL_INDEX_CONFIGURATION_MISMATCH"):
        asyncio.run(repo.index_status(create=True))
    db["visual_vectors"].create_search_index.assert_not_awaited()


def test_local_geometry_recovers_transformed_patch_and_rejects_unrelated_features():
    cv = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(7)
    image = rng.integers(0, 256, (400, 400), dtype=np.uint8)
    image = cv.GaussianBlur(image, (3, 3), 0)
    partial = image[70:330, 60:340]
    transform = np.float32([[1, 0.07, 4], [0.03, 1, 2], [0.0001, 0.0001, 1]])
    warped = cv.warpPerspective(partial, transform, (280, 260))

    def describe(data):
        points, descriptors = cv.SIFT_create(nfeatures=800).detectAndCompute(data, None)
        return {
            "points": [list(p.pt) for p in points],
            "descriptors": descriptors.tolist(),
        }

    ref = describe(image)
    query = describe(warped)
    assert geometric_score(query, ref) > 0.7
    other = describe(
        cv.GaussianBlur(rng.integers(0, 256, (400, 400), dtype=np.uint8), (3, 3), 0)
    )
    assert geometric_score(query, other) == 0.0


def photo(**changes):
    return Photo(
        **{
            "id": "p1",
            "path": "photo.png",
            "location_id": L1,
            "expected_object_id": "123456789012345678901234",
            "capture_group": "capture1",
            "split": "evaluation",
            "provenance": "synthetic fixture",
            "independent": False,
            "category": "partial",
            "object_type": "building",
            "viewpoint": "side",
            **changes,
        }
    )


def test_capture_leakage_rejected_before_benchmark():
    with pytest.raises(VisualFailure, match="BENCHMARK_CAPTURE_LEAKAGE"):
        validate_manifest([photo(), photo(id="p2", split="calibration")])


def test_automatic_policy_requires_score_and_margin():
    policy = Thresholds(minimum=0.4, automatic=0.8, margin=0.1)
    assert (
        select(
            [{"object_id": "a", "score": 0.9}, {"object_id": "b", "score": 0.85}],
            policy,
        )
        is None
    )
    assert select([{"object_id": "a", "score": 0.7}], policy) is None
    assert (
        select(
            [{"object_id": "a", "score": 0.95}, {"object_id": "b", "score": 0.5}],
            policy,
        )
        == "a"
    )


def test_synthetic_benchmark_cannot_pass_acceptance_gates(setup, tmp_path):
    db, repo, encoder = setup
    asyncio.run(prepare_one(setup))
    (tmp_path / "photo.png").write_bytes(picture())
    policy = BenchmarkPolicy(
        model=encoder.model,
        preprocessing=encoder.preprocessing,
        methods={
            m: Thresholds(minimum=0.0, automatic=0.8, margin=0.1)
            for m in ("global", "regional", "local")
        },
    )
    photos = [photo(expected_object_id=str(db["objects"].documents[0]["_id"]))]
    report = asyncio.run(
        benchmark(repo, encoder, photos, tmp_path, policy, "evaluation")
    )
    assert report["phase5_complete"] is False
    assert report["methods"]["regional"]["top3_correct"] == 1
    assert report["methods"]["regional"]["recognition_gates_passed"] is False


def test_upload_visual_mode_does_not_invoke_ocr(setup):
    _, repo, encoder = setup
    app = create_app()
    ocr = SimpleNamespace(recognize=AsyncMock())
    app.dependency_overrides[get_service] = lambda: ocr
    app.state.visual_encoder = encoder
    app.state.visual_lock = asyncio.Lock()
    with patch("app.modules.recognition.router.get_database", return_value=repo.db):
        response = TestClient(app).post(
            "/api/v1/recognition/image",
            data={"location_id": L1, "mode": "visual"},
            files={"image": ("photo.png", picture(), "image/png")},
        )
    assert (
        response.status_code == 200 and response.json()["method"] == "visual_embeddings"
    )
    ocr.recognize.assert_not_awaited()
