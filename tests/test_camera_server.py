"""The public frame route is bounded, location-scoped and memory-only."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.modules.live_camera.inference import CameraFailure, CameraRecognizer
from app.modules.live_camera.server import MAX_FRAME_BYTES, create_camera_app


class FakeRecognizer(CameraRecognizer):
    def __init__(self):
        self.reference_version = "a" * 64
        self.catalog = {"location": {"id": "b" * 24}}
        self.expires = datetime.now(UTC) + timedelta(days=1)
        self.calls = []

    def warm(self):
        self.warmed = True

    def recognize(self, data):
        self.calls.append(data)
        return {"candidates": [], "automatic_identification": False}


@pytest.fixture
def camera(tmp_path):
    engine = FakeRecognizer()
    client = TestClient(create_camera_app(tmp_path, engine))
    url = f"/recognize?location_id={'b' * 24}&reference_version={'a' * 64}"
    return engine, client, url


def jpeg(size=(224, 224)):
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format="JPEG")
    return out.getvalue()


def test_readiness_warms_without_recognizing_or_writing_frames(camera, tmp_path):
    engine, client, _ = camera
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["automatic_identification"] is False
    assert engine.warmed and engine.calls == []
    assert list(tmp_path.iterdir()) == []


def test_frame_reaches_engine_and_immediate_repeat_is_throttled(camera, tmp_path):
    engine, client, url = camera
    data = jpeg()
    response = client.post(url, content=data, headers={"Content-Type": "image/jpeg"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert engine.calls == [data]
    assert list(tmp_path.iterdir()) == []
    assert (
        client.post(
            url, content=data, headers={"Content-Type": "image/jpeg"}
        ).status_code
        == 429
    )


@pytest.mark.parametrize(
    "case,code",
    [
        ("large", 413),
        ("chunked", 413),
        ("empty", 422),
        ("type", 415),
        ("location", 409),
        ("version", 409),
        ("origin", 403),
    ],
)
def test_rejects_invalid_requests_before_inference(camera, case, code):
    engine, client, url = camera
    content = jpeg()
    headers = {"Content-Type": "image/jpeg"}
    if case == "large":
        content = b"x" * (MAX_FRAME_BYTES + 1)
    if case == "chunked":
        content = iter([b"x" * (MAX_FRAME_BYTES // 2)] * 2 + [b"x"])
    if case == "empty":
        content = b""
    if case == "type":
        headers["Content-Type"] = "text/plain"
    if case == "location":
        url = url.replace("b" * 24, "c" * 24)
    if case == "version":
        url = url.replace("a" * 64, "c" * 64)
    if case == "origin":
        headers["Sec-Fetch-Site"] = "cross-site"
    assert client.post(url, content=content, headers=headers).status_code == code
    assert engine.calls == []


def test_expired_reference_and_busy_engine_do_not_infer(camera):
    engine, client, url = camera
    engine.expires = datetime.now(UTC) - timedelta(seconds=1)
    assert client.post(
        url, content=jpeg(), headers={"Content-Type": "image/jpeg"}
    ).json() == {"error": "REFERENCES_EXPIRED"}
    assert engine.calls == []


@pytest.mark.parametrize("data", [b"invalid", jpeg((225, 224)), jpeg((1, 1))])
def test_native_decoder_rejects_invalid_or_wrong_sized_images(data):
    pytest.importorskip("numpy")
    engine = CameraRecognizer.__new__(CameraRecognizer)
    engine.adaptive = False
    engine.expires = datetime.now(UTC) + timedelta(days=1)
    with pytest.raises(CameraFailure, match="INVALID_FRAME"):
        engine.recognize(data)


def test_internal_failure_is_sanitized(camera):
    engine, client, url = camera

    def broken(data):
        raise RuntimeError("private internal details")

    engine.recognize = broken
    response = client.post(url, content=jpeg(), headers={"Content-Type": "image/jpeg"})
    assert response.status_code == 503
    assert response.json() == {"error": "RECOGNITION_UNAVAILABLE"}


def test_concurrent_frame_is_rejected_without_starting_more_inference(camera):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    engine, client, url = camera
    entered, release = Event(), Event()

    def slow(data):
        entered.set()
        assert release.wait(5)
        return {"candidates": []}

    engine.recognize = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post, url, content=jpeg(), headers={"Content-Type": "image/jpeg"}
        )
        try:
            assert entered.wait(5)
            response = client.post(
                url, content=jpeg(), headers={"Content-Type": "image/jpeg"}
            )
            assert response.status_code == 429
        finally:
            release.set()
        assert pending.result().status_code == 200


def test_geometry_rejects_visual_resemblance_without_matching_details():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from app.modules.live_camera.geometry import describe_local, validate_local

    rng = np.random.default_rng(42)
    image = Image.fromarray(rng.integers(0, 256, (224, 224, 3), dtype=np.uint8))
    other = Image.fromarray(rng.integers(0, 256, (224, 224, 3), dtype=np.uint8))
    data = io.BytesIO()
    image.save(data, format="JPEG", quality=90)
    decoded = Image.open(io.BytesIO(data.getvalue())).convert("RGB")
    reference = describe_local(decoded)
    unrelated = describe_local(other)
    validate_local(reference)
    validate_local(unrelated)
    vector = np.zeros((1, 384), dtype=np.float32)
    vector[0, 0] = 1

    class Session:
        def run(self, *args):
            return [vector]

    engine = CameraRecognizer.__new__(CameraRecognizer)
    engine.expires = datetime.now(UTC) + timedelta(days=1)
    engine.reference_version = "a" * 64
    engine.catalog = {"location": {"id": "b" * 24}}
    engine.session = Session()
    engine.views = [("correct", [vector]), ("unrelated", [vector])]
    engine.adaptive = False
    engine.geometry_enabled = True
    engine.local_views = {"correct": [reference], "unrelated": [unrelated]}
    result = engine.recognize(data.getvalue())
    assert result["candidates"] == [
        {"id": "correct", "score": 1.0},
        {"id": "unrelated", "score": 0.0},
    ]
    assert result["automatic_identification"] is False


def test_geometry_must_verify_the_view_that_supplies_the_embedding(monkeypatch):
    np = pytest.importorskip("numpy")
    vector = np.zeros((1, 384), dtype=np.float32)
    vector[0, 0] = 1

    def reference(similarity):
        value = vector.copy()
        value[0, 0] = similarity
        value[0, 1] = (1 - similarity**2) ** 0.5
        return value

    class Session:
        def run(self, *args):
            return [vector]

    engine = CameraRecognizer.__new__(CameraRecognizer)
    engine.expires = datetime.now(UTC) + timedelta(days=1)
    engine.reference_version = "a" * 64
    engine.catalog = {"location": {"id": "b" * 24}}
    engine.session = Session()
    engine.geometry_enabled = True
    # A room's highly similar view has no matching detail. Another room view
    engine.adaptive = False
    # matches a small detail but cannot verify the first view's embedding.
    engine.views = [
        ("room", [reference(0.95), reference(0.2)]),
        ("artwork", [reference(0.8)]),
    ]
    engine.local_views = {
        "room": [{"geometry": 0.0}, {"geometry": 0.8}],
        "artwork": [{"geometry": 0.8}],
    }
    monkeypatch.setattr(
        "app.modules.live_camera.inference.describe_local", lambda image: {}
    )
    monkeypatch.setattr(
        "app.modules.live_camera.inference.geometric_score",
        lambda query, ref: ref["geometry"],
    )
    result = engine.recognize(jpeg())
    assert [candidate["id"] for candidate in result["candidates"]] == [
        "artwork",
        "room",
    ]
    assert result["candidates"][1]["score"] == pytest.approx(0.2)
    assert result["automatic_identification"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 801])
def test_geometry_rejects_invalid_reference_coordinates(bad):
    pytest.importorskip("numpy")
    from app.modules.live_camera.geometry import validate_local

    value = {
        "points": [[bad, 1]] * 8,
        "descriptors": [[0.0] * 128] * 8,
        "size": [224, 224],
    }
    with pytest.raises(ValueError, match="Invalid camera local features"):
        validate_local(value)
