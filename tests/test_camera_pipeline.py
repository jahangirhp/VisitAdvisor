import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, ImageDraw

from app.modules.live_camera.inference import CameraFailure, CameraRecognizer
from app.modules.live_camera.pipeline import PIPELINE_VERSION, view_coverage
from app.modules.live_camera.regions import FULL_FRAME, overlap, proposals


def frame():
    image = Image.new("RGB", (224, 224), "gray")
    ImageDraw.Draw(image).rectangle(
        (55, 35, 168, 190), fill="blue", outline="white", width=4
    )
    data = io.BytesIO()
    image.save(data, "JPEG", quality=90)
    return data.getvalue()


def engine(tmp_path, types=("sculpture", "building"), regions=False, complete=True):
    np = pytest.importorskip("numpy")

    def vector(score):
        value = [0.0] * 384
        value[0], value[1] = score, (1 - score**2) ** 0.5
        return value

    objects = []
    for index, kind in enumerate(types):
        views = []
        for side in range(2 if complete else 1):
            views.append(
                {
                    "vectors": [vector(0.95 - index * 0.3 - side * 0.05)],
                    "image_hash": f"image-{index}-{side}",
                    "viewpoint": "front" if side == 0 else "back",
                    "local": {
                        "points": [[i + 1, i + 1] for i in range(8)],
                        "descriptors": [[0.0] * 128] * 8,
                        "size": [224, 224],
                    },
                }
            )
        objects.append(
            {
                "id": str(index),
                "location_id": "location",
                "type": kind,
                "sources": [{"id": "canonical-source"}],
                "views": views,
            }
        )
    catalog = {
        "version": "live-camera-v1",
        "reference_version": "a" * 64,
        "location": {"id": "location"},
        "objects": objects,
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "automatic_identification": False,
        "model": {"dimensions": 384, "input_size": 224, "sha256": "b" * 64},
        "policy": {
            "global_weight": 0.75,
            "calibrated": False,
            "candidate_floor": 0.55,
            "candidate_margin": 0.04,
            "stable_frames": 3,
            "geometry_min_score": 0.4,
            "recognition_pipeline": PIPELINE_VERSION,
            "object_regions": regions,
            "inference_budget_ms": 1500,
            "profiles": {
                kind: {"floor": 0.55, "margin": 0.04, "enabled": True}
                for kind in ("planar", "multiview")
            },
        },
    }

    class Session:
        def __init__(self):
            self.calls = 0

        def run(self, *args):
            self.calls += 1
            return [np.asarray([[1.0] + [0.0] * 383], dtype=np.float32)]

    return CameraRecognizer(tmp_path, catalog, Session())


def test_sculptures_use_view_vectors_without_planar_geometry(tmp_path, monkeypatch):
    value = engine(tmp_path, types=("sculpture", "artifact"))
    monkeypatch.setattr(
        "app.modules.live_camera.pipeline.geometric_evidence",
        lambda *args: pytest.fail("Planar geometry applied to 3D objects"),
    )
    result = value.recognize(frame())
    assert result["candidates"][0]["id"] == "0"
    assert result["candidates"][0]["verification"] == "multiview_embeddings"
    assert result["candidates"][0]["reference_views"] == 2
    assert result["suggestion_allowed"]
    assert result["automatic_identification"] is False


def test_one_view_is_not_sufficient_multi_view_coverage(tmp_path):
    value = engine(tmp_path, complete=False)
    result = value.recognize(frame())
    assert result["candidates"][0]["id"] == "0"
    assert not result["suggestion_allowed"]
    assert result["decision"] == "ambiguous"
    assert "REFERENCE_VIEW_COVERAGE_INCOMPLETE" in result["warnings"]


def test_uncalibrated_scores_never_establish_a_suggestion(tmp_path):
    value = engine(tmp_path)
    value.catalog["policy"].pop("profiles")
    result = value.recognize(frame())
    assert result["candidates"][0]["score"] > 0.9
    assert not result["suggestion_allowed"]
    assert "CAMERA_CALIBRATION_REQUIRED" in result["warnings"]


def test_duplicate_photos_or_labels_cannot_claim_multi_view_coverage(tmp_path):
    value = engine(tmp_path)
    obj = value.catalog["objects"][0]
    obj["views"][1]["image_hash"] = obj["views"][0]["image_hash"]
    assert not view_coverage(obj)["ready"]
    obj["views"][1]["image_hash"] = "different-image"
    obj["views"][1]["viewpoint"] = "front"
    assert not view_coverage(obj)["ready"]


def test_painting_similarity_cannot_bypass_geometry(tmp_path, monkeypatch):
    value = engine(tmp_path, types=("painting", "painting"))
    monkeypatch.setattr(
        "app.modules.live_camera.pipeline.geometric_evidence",
        lambda *args: {"score": 0.0},
    )
    result = value.recognize(frame())
    assert all(c["score"] == 0 for c in result["candidates"])
    assert not result["suggestion_allowed"]


def test_local_details_recover_painting_outside_embedding_top_three(
    tmp_path, monkeypatch
):
    value = engine(tmp_path, types=("painting",) * 4)
    target = value.local_views["3"][1]
    visited = []

    def evidence(query, reference):
        visited.append(reference)
        return {"score": 0.92 if reference is target else 0.0}

    monkeypatch.setattr("app.modules.live_camera.pipeline.geometric_evidence", evidence)
    result = value.recognize(frame())
    first = result["candidates"][0]
    assert first["id"] == "3"
    assert first["view_index"] == 1
    assert first["score"] == 0.92
    assert first["verification"] == "planar_geometry"
    assert len(visited) == 8
    assert result["suggestion_allowed"]


def test_verification_budget_stops_between_reference_views(tmp_path, monkeypatch):
    value = engine(tmp_path, types=("painting",) * 4)
    clock = [0.0]
    visited = []
    monkeypatch.setattr(
        "app.modules.live_camera.pipeline.perf_counter", lambda: clock[0]
    )

    def evidence(query, reference):
        visited.append(reference)
        clock[0] += 2
        return {"score": 0.99}

    monkeypatch.setattr("app.modules.live_camera.pipeline.geometric_evidence", evidence)
    from app.modules.live_camera.pipeline import recognize

    result = recognize(value, Image.open(io.BytesIO(frame())), 0)
    assert len(visited) == 1
    assert result["decision"] == "deferred"
    assert not result["suggestion_allowed"]


def test_featureless_frame_skips_model_work(tmp_path):
    value = engine(tmp_path)
    data = io.BytesIO()
    Image.new("RGB", (224, 224), "white").save(data, "JPEG")
    result = value.recognize(data.getvalue())
    assert result["decision"] == "unreadable"
    assert value.session.calls == 0
    assert result["timings"]["model_calls"] == 0


def test_detailed_frame_preserves_local_resolution_but_encoder_stays_224(
    tmp_path, monkeypatch
):
    import numpy as np

    value = engine(tmp_path, types=("painting", "painting"))
    image = Image.open(io.BytesIO(frame())).resize((512, 512))
    data = io.BytesIO()
    image.save(data, "JPEG", quality=90)
    tensor_sizes = []
    local_sizes = []

    def run(outputs, inputs):
        tensor_sizes.append(inputs["pixel_values"].shape)
        return [np.asarray([[1.0] + [0.0] * 383], dtype=np.float32)]

    def evidence(query, reference):
        local_sizes.append(query["size"])
        return {"score": 0.0}

    value.session.run = run
    monkeypatch.setattr("app.modules.live_camera.pipeline.geometric_evidence", evidence)
    result = value.recognize(data.getvalue())
    assert tensor_sizes == [(1, 3, 224, 224)]
    assert local_sizes == [[512, 512]] * 4
    assert not result["suggestion_allowed"]


def test_model_calls_are_bounded_and_budget_overrun_never_suggests(
    tmp_path, monkeypatch
):
    value = engine(tmp_path, regions=True)
    monkeypatch.setattr(
        "app.modules.live_camera.pipeline.proposals",
        lambda *args: [(0.1, 0.1, 0.6, 0.6), (0.4, 0.4, 0.9, 0.9)],
    )
    result = value.recognize(frame())
    assert result["timings"]["model_calls"] == value.session.calls == 3
    clock = [0.0]
    value.catalog["policy"]["inference_budget_ms"] = 250
    monkeypatch.setattr(
        "app.modules.live_camera.pipeline.perf_counter", lambda: clock[0]
    )

    def slow_embedding(*args):
        import numpy as np

        clock[0] += 0.3
        return np.asarray([1.0] + [0.0] * 383)

    monkeypatch.setattr("app.modules.live_camera.pipeline.embedding", slow_embedding)
    from app.modules.live_camera.pipeline import recognize

    result = recognize(value, Image.open(io.BytesIO(frame())), 0)
    assert result["timings"]["model_calls"] == 1
    assert result["decision"] == "deferred"
    assert not result["suggestion_allowed"]


def test_warmup_runs_once_and_does_not_create_candidates(tmp_path):
    value = engine(tmp_path)
    value.warm()
    value.warm()
    assert value.warmed and value.session.calls == 1
    assert value.public_catalog()["readiness"]["model_warmed"]
    assert "views" not in value.public_catalog()["objects"][0]


def test_failed_warmup_cannot_mark_model_ready(tmp_path):
    value = engine(tmp_path)
    value.session.run = lambda *args: (_ for _ in ()).throw(
        RuntimeError("private details")
    )
    with pytest.raises(CameraFailure, match="^MODEL_WARMUP_FAILED$"):
        value.warm()
    assert not value.warmed


def test_region_proposals_are_bounded_and_silhouettes_are_not_identities():
    image = Image.open(io.BytesIO(frame()))
    boxes = proposals(image)
    assert 1 <= len(boxes) <= 2
    assert all(0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1 for b in boxes)
    assert proposals(Image.new("RGB", (224, 224), "white")) == []
    assert overlap(FULL_FRAME, FULL_FRAME) == 1
    with pytest.raises(ValueError):
        proposals(image, maximum=3)
