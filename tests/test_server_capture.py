"""Selected-still API bounds, source linkage and interruptible worker lifecycle."""

import asyncio
import base64
import hashlib
import io
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.main import create_app
from app.modules.admin import server_capture
from app.modules.admin.server_capture import (
    AnalysisPrompt,
    CaptureAnalyzer,
    read_frame,
    validate_frame,
)
from app.modules.admin.server_model import MODEL_ID, verify_model
from scripts import prepare_server_capture


def jpeg(size=(80, 60), exif=None):
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, "JPEG", exif=exif or b"")
    return output.getvalue()


def prompt(**changes):
    return AnalysisPrompt(
        **{
            "session_id": "session-1",
            "frame_id": "frame-1",
            "profile": "multiview",
            "point_x": 0.5,
            "point_y": 0.5,
            **changes,
        }
    )


def candidate(size=(80, 60)):
    output = io.BytesIO()
    Image.new("L", size, 255).save(output, "PNG")
    data = output.getvalue()
    return {
        "id": "mask-0",
        "score": 0.55,
        "target_box": [0, 0, 1, 1],
        "mask_png_base64": base64.b64encode(data).decode("ascii"),
        "mask_sha256": hashlib.sha256(data).hexdigest(),
    }


def client(enabled=True):
    application = create_app()
    application.state.settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://localhost/test",
        admin_token="test-token",
        admin_capture_server_enabled=enabled,
    )
    analyzer = CaptureAnalyzer(Path("unused"))
    analyzer.infer = AsyncMock(
        return_value={"candidates": [candidate()], "inference_ms": 10}
    )
    application.state.admin_capture_analyzer = analyzer
    return TestClient(application), analyzer


def post(client, params=None, data=None, **kwargs):
    return client.post(
        "/api/v1/admin/capture/analyze",
        params=params or prompt().model_dump(exclude_none=True),
        content=jpeg() if data is None else data,
        headers={"Authorization": "Bearer test-token", "Content-Type": "image/jpeg"},
        **kwargs,
    )


def test_api_is_authenticated_opt_in_and_never_uses_database():
    http, analyzer = client()
    response = http.post("/api/v1/admin/capture/analyze")
    assert response.status_code == 401
    http.app.state.settings.admin_token = None
    assert post(http).status_code == 503
    analyzer.infer.assert_not_called()
    disabled, analyzer = client(enabled=False)
    response = post(disabled)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CAPTURE_ANALYSIS_DISABLED"
    analyzer.infer.assert_not_called()


def test_candidates_link_exact_source_without_storing_or_auto_selection():
    http, analyzer = client()
    image = jpeg()
    response = post(http, data=image)
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert result["source_sha256"] == hashlib.sha256(image).hexdigest()
    assert (result["width"], result["height"]) == (80, 60)
    assert result["session_id"] == "session-1"
    assert result["frame_id"] == "frame-1"
    assert result["version"] == "server-capture-v1"
    assert result["model"]["name"] == MODEL_ID
    assert result["candidates"] == [candidate()]
    assert "selected" not in result
    assert not hasattr(http.app.state, "mongodb")
    assert analyzer.infer.call_args.args[0] == image
    assert analyzer.sessions["session-1"].byte_count == len(image)
    assert set(vars(analyzer.sessions["session-1"])) == {
        "started",
        "calls",
        "byte_count",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"point_x": float("nan")},
        {"point_y": 1.1},
        {"session_id": "../session"},
        {"profile": "arbitrary prompt"},
        {"box_x1": 0.1},
        {"box_x1": 0.8, "box_y1": 0.1, "box_x2": 0.2, "box_y2": 0.9},
        {"box_x1": 0.6, "box_y1": 0.1, "box_x2": 0.8, "box_y2": 0.9},
    ],
)
def test_prompt_rejects_bad_coordinates_and_partial_or_reversed_boxes(changes):
    with pytest.raises(ValidationError):
        prompt(**changes)


def test_box_prompt_is_preserved_and_extra_arbitrary_input_is_rejected():
    http, analyzer = client()
    params = prompt(box_x1=0.1, box_y1=0.2, box_x2=0.8, box_y2=0.9).model_dump(
        exclude_none=True
    )
    assert post(http, params).status_code == 200
    assert analyzer.infer.call_args.args[1].box == (0.1, 0.2, 0.8, 0.9)
    assert (
        post(http, {**params, "remote_url": "https://example.com"}).status_code == 422
    )


def test_raw_body_limit_actual_type_and_dimensions_precede_inference():
    http, analyzer = client()
    assert (
        post(http, data=b"x" * (server_capture.MAX_ANALYSIS_BYTES + 1)).status_code
        == 413
    )
    assert post(http, data=b"invalid").status_code == 422
    assert post(http, data=jpeg((1601, 1))).status_code == 413
    png = io.BytesIO()
    Image.new("RGB", (2, 2)).save(png, "PNG")
    assert post(http, data=png.getvalue()).status_code == 415
    analyzer.infer.assert_not_called()


def test_unsupported_media_orientation_and_damaged_jpeg():
    http, analyzer = client()
    response = http.post(
        "/api/v1/admin/capture/analyze",
        params=prompt().model_dump(exclude_none=True),
        content=jpeg(),
        headers={"Authorization": "Bearer test-token", "Content-Type": "image/png"},
    )
    assert response.status_code == 415
    exif = Image.Exif()
    exif[274] = 6
    assert post(http, data=jpeg(exif=exif)).status_code == 422
    assert post(http, data=jpeg()[:150]).status_code == 422
    assert validate_frame(jpeg((1600, 1600))) == (1600, 1600)
    analyzer.infer.assert_not_called()


def test_chunked_body_is_bounded_and_has_deadline(monkeypatch):
    async def run():
        calls = 0

        async def receive():
            nonlocal calls
            calls += 1
            return {"type": "http.request", "body": b"x" * 600_000, "more_body": True}

        request = Request(
            {"type": "http", "headers": [(b"content-type", b"image/jpeg")]}, receive
        )
        with pytest.raises(ApplicationError) as error:
            await read_frame(request)
        assert error.value.code == "CAPTURE_ANALYSIS_TOO_LARGE"
        assert calls == 2
        monkeypatch.setattr(server_capture, "BODY_TIMEOUT_SECONDS", 0.01)

        async def stalled():
            await asyncio.sleep(10)

        request = Request(
            {"type": "http", "headers": [(b"content-type", b"image/jpeg")]}, stalled
        )
        with pytest.raises(ApplicationError) as error:
            await read_frame(request)
        assert error.value.code == "CAPTURE_ANALYSIS_TIMEOUT"

    asyncio.run(run())


def test_busy_request_has_no_queue_or_inference():
    http, analyzer = client()

    async def run():
        await analyzer.lock.acquire()
        try:
            with pytest.raises(ApplicationError) as error:
                await analyzer.analyze(prompt(), None)
            assert error.value.code == "CAPTURE_ANALYSIS_BUSY"
        finally:
            analyzer.lock.release()

    asyncio.run(run())
    analyzer.infer.assert_not_called()


def test_session_global_and_retained_counter_limits(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(server_capture.time, "monotonic", lambda: clock[0])
    analyzer = CaptureAnalyzer(Path("unused"))
    for _ in range(server_capture.MAX_SESSION_CALLS):
        analyzer.reserve("one", 10)
    with pytest.raises(ApplicationError) as error:
        analyzer.reserve("one", 10)
    assert error.value.code == "CAPTURE_ANALYSIS_LIMIT"
    for index in range(server_capture.MAX_STARTS_PER_MINUTE - 12):
        analyzer.reserve(str(index), 10)
    with pytest.raises(ApplicationError):
        analyzer.reserve("new", 10)
    clock[0] += 61
    for index in range(32 - len(analyzer.sessions)):
        analyzer.reserve(f"extra-{index}", 10)
    assert len(analyzer.sessions) == 32
    with pytest.raises(ApplicationError):
        analyzer.reserve("over-limit", 10)
    clock[0] += 301
    analyzer.reserve("fresh", 10)
    assert set(analyzer.sessions) == {"fresh"}


class FakeProcess:
    alive = True

    def is_alive(self):
        return self.alive


class StalledConnection:
    sent = False

    def send(self, message):
        self.sent = True

    def poll(self):
        return False


def test_timeout_closes_child_even_when_disconnected_without_leaking_errors(
    monkeypatch,
):
    async def run(disconnected):
        analyzer = CaptureAnalyzer(Path("unused"))
        analyzer.process = FakeProcess()
        analyzer.connection = StalledConnection()
        analyzer.ready = True
        monkeypatch.setattr(analyzer, "start", lambda: None)
        closed = []
        monkeypatch.setattr(analyzer, "close", lambda: closed.append(True))
        request = AsyncMock()
        request.is_disconnected.return_value = disconnected
        with pytest.raises(ApplicationError) as error:
            await analyzer.infer(jpeg(), prompt(), request)
        assert error.value.code == "CAPTURE_ANALYSIS_TIMEOUT"
        assert closed == [True]

    monkeypatch.setattr(server_capture, "ANALYSIS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(server_capture, "MODEL_START_TIMEOUT_SECONDS", 0.01)
    asyncio.run(run(False))
    asyncio.run(run(True))


@pytest.mark.parametrize("invalid_reply", [False, True])
def test_disconnect_drains_under_lock_and_reuses_only_a_valid_ready_child(
    monkeypatch, invalid_reply
):
    class CompletingConnection:
        def __init__(self):
            self.started = None
            self.images = 0

        def send(self, _message):
            self.images += 1
            self.started = time.monotonic()

        def poll(self):
            return self.started is not None and time.monotonic() - self.started >= 0.04

        def recv(self):
            return (
                {"ok": {"candidates": [], "inference_ms": -1}}
                if invalid_reply
                else {"ok": {"candidates": [], "inference_ms": 1}}
            )

    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))
        analyzer.process = FakeProcess()
        analyzer.connection = CompletingConnection()
        analyzer.ready = True
        start, close = Mock(), Mock()
        monkeypatch.setattr(analyzer, "start", start)
        monkeypatch.setattr(analyzer, "close", close)
        monkeypatch.setattr(
            server_capture, "read_frame", AsyncMock(return_value=jpeg())
        )
        request = AsyncMock()
        request.is_disconnected.return_value = True
        task = asyncio.create_task(analyzer.analyze(prompt(), request))
        await asyncio.sleep(0.01)
        assert analyzer.lock.locked()
        assert not task.done()
        with pytest.raises(ApplicationError) as busy:
            await analyzer.analyze(prompt(), request)
        assert busy.value.code == "CAPTURE_ANALYSIS_BUSY"
        with pytest.raises(ApplicationError) as error:
            await task
        assert not analyzer.lock.locked()
        if invalid_reply:
            assert error.value.code == "CAPTURE_ANALYSIS_UNAVAILABLE"
            close.assert_called_once()
            return
        assert error.value.code == "CAPTURE_ANALYSIS_CANCELLED"
        close.assert_not_called()
        assert analyzer.ready
        request.is_disconnected.return_value = False
        result = await analyzer.analyze(prompt(frame_id="frame-2"), request)
        assert result.frame_id == "frame-2"
        assert result.candidates == []
        assert analyzer.connection.images == 2
        start.assert_not_called()
        close.assert_not_called()

    asyncio.run(run())


def test_disconnect_during_startup_finishes_readiness_without_sending_photo(
    monkeypatch,
):
    class StartingConnection:
        def __init__(self):
            self.started = time.monotonic()
            self.send = Mock()

        def poll(self):
            return time.monotonic() - self.started >= 0.04

        def recv(self):
            return {"ready": True}

    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))
        analyzer.process = FakeProcess()
        analyzer.connection = StartingConnection()
        close = Mock()
        monkeypatch.setattr(analyzer, "start", lambda: None)
        monkeypatch.setattr(analyzer, "close", close)
        request = AsyncMock()
        request.is_disconnected.return_value = True
        with pytest.raises(ApplicationError) as error:
            await analyzer.infer(jpeg(), prompt(), request)
        assert error.value.code == "CAPTURE_ANALYSIS_CANCELLED"
        assert analyzer.ready
        analyzer.connection.send.assert_not_called()
        close.assert_not_called()

    asyncio.run(run())


def test_missing_or_corrupt_model_is_verified_without_network(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Verification must not download model files")

    monkeypatch.setattr(prepare_server_capture, "urlopen", forbidden)
    with pytest.raises(OSError):
        verify_model(tmp_path)
    (tmp_path / "config.json").write_text("bad")
    with pytest.raises(ValueError):
        verify_model(tmp_path)


def test_cold_start_deadline_keeps_event_loop_responsive_and_cancellation_closes_child(
    monkeypatch,
):
    async def run(cancel):
        analyzer = CaptureAnalyzer(Path("unused"))
        connection = StalledConnection()

        def start():
            analyzer.process = FakeProcess()
            analyzer.connection = connection

        monkeypatch.setattr(analyzer, "start", start)
        closed = []
        monkeypatch.setattr(analyzer, "close", lambda: closed.append(True))
        request = AsyncMock()
        request.is_disconnected.return_value = False
        task = asyncio.create_task(analyzer.infer(jpeg(), prompt(), request))
        await asyncio.sleep(0.002)
        assert not task.done()
        assert not connection.sent  # The image waits for successful model readiness.
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ApplicationError) as error:
                await task
            assert error.value.code == "CAPTURE_ANALYSIS_TIMEOUT"
        assert closed == [True]

    monkeypatch.setattr(server_capture, "MODEL_START_TIMEOUT_SECONDS", 0.01)
    asyncio.run(run(False))
    asyncio.run(run(True))


def test_idle_model_stays_resident_without_retaining_a_source_frame(monkeypatch):
    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))
        analyzer.process = FakeProcess()
        analyzer.infer = AsyncMock(return_value={"candidates": [], "inference_ms": 1})
        closed = []
        monkeypatch.setattr(analyzer, "close", lambda: closed.append(True))

        async def receive():
            return {"type": "http.request", "body": jpeg(), "more_body": False}

        request = Request(
            {"type": "http", "headers": [(b"content-type", b"image/jpeg")]}, receive
        )
        await analyzer.analyze(prompt(), request)
        assert not closed
        await asyncio.sleep(0.02)
        assert not closed
        assert not hasattr(analyzer, "idle_timer")
        assert set(vars(analyzer.sessions["session-1"])) == {
            "started",
            "calls",
            "byte_count",
        }

    asyncio.run(run())


def test_preparation_is_shared_and_first_photo_uses_the_ready_child(monkeypatch):
    class WarmConnection:
        def __init__(self):
            self.messages = []
            self.reads = 0

        def poll(self):
            return True

        def recv(self):
            self.reads += 1
            return (
                {"ok": {"candidates": [], "inference_ms": 1}}
                if self.messages
                else {"ready": True}
            )

        def send(self, message):
            self.messages.append(message)

    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))
        connection = WarmConnection()
        starts = []

        def start():
            starts.append(True)
            analyzer.process = FakeProcess()
            analyzer.connection = connection

        monkeypatch.setattr(analyzer, "start", start)
        await asyncio.gather(analyzer.prepare(), analyzer.prepare())
        assert analyzer.ready
        assert starts == [True]
        assert connection.reads == 1
        assert connection.messages == []
        request = AsyncMock()
        request.is_disconnected.return_value = False
        assert await analyzer.infer(jpeg(), prompt(), request) == {
            "candidates": [],
            "inference_ms": 1,
        }
        assert starts == [True]
        assert connection.reads == 2
        assert len(connection.messages) == 1
        analyzer.process.alive = False
        connection.messages.clear()
        await analyzer.prepare()
        assert starts == [True, True]
        assert analyzer.ready

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_preparation_timeout_or_cancel_closes_the_unready_child(monkeypatch, cancel):
    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))

        def start():
            analyzer.process = FakeProcess()
            analyzer.connection = StalledConnection()

        monkeypatch.setattr(analyzer, "start", start)
        closed = []
        monkeypatch.setattr(analyzer, "close", lambda: closed.append(True))
        task = asyncio.create_task(analyzer.prepare())
        await asyncio.sleep(0.002)
        assert analyzer.lock.locked()
        assert not analyzer.ready
        with pytest.raises(ApplicationError) as busy:
            await analyzer.analyze(prompt(), None)
        assert busy.value.code == "CAPTURE_ANALYSIS_BUSY"
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ApplicationError) as error:
                await task
            assert error.value.code == "CAPTURE_ANALYSIS_TIMEOUT"
        assert closed == [True]
        assert not analyzer.ready
        assert not analyzer.lock.locked()

    monkeypatch.setattr(server_capture, "MODEL_START_TIMEOUT_SECONDS", 0.01)
    asyncio.run(run())


def test_preparation_spawn_failure_closes_pipes_and_returns_a_safe_error(monkeypatch):
    parent, child, process = Mock(), Mock(), Mock()
    process.start.side_effect = OSError("private runtime details")
    context = Mock()
    context.Pipe.return_value = (parent, child)
    context.Process.return_value = process
    monkeypatch.setattr(
        server_capture.multiprocessing, "get_context", lambda _: context
    )
    analyzer = CaptureAnalyzer(Path("unused"))
    with pytest.raises(ApplicationError) as error:
        asyncio.run(analyzer.prepare())
    assert error.value.code == "CAPTURE_ANALYSIS_UNAVAILABLE"
    assert "private runtime details" not in str(error.value)
    assert analyzer.process is None
    assert analyzer.connection is None
    assert not analyzer.ready
    parent.close.assert_called_once()
    child.close.assert_called_once()
    process.close.assert_called_once()


@pytest.mark.parametrize(
    "enabled,failed", [(True, False), (True, True), (False, False)]
)
def test_lifespan_warms_enabled_capture_before_serving_and_keeps_fallback_healthy(
    monkeypatch, caplog, enabled, failed
):
    from app import main

    monkeypatch.setattr(main, "configure_logging", lambda: None)
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://localhost/test",
        admin_capture_server_enabled=enabled,
    )
    monkeypatch.setattr(main, "Settings", lambda: settings)
    database = SimpleNamespace(
        ping=AsyncMock(), ensure_indexes=AsyncMock(), close=AsyncMock()
    )
    monkeypatch.setattr(main, "MongoDB", lambda _settings: database)
    analyzer = SimpleNamespace(prepare=AsyncMock(), close=Mock())
    if failed:
        analyzer.prepare.side_effect = server_capture.failure(
            "UNAVAILABLE", "Photo analysis is unavailable.", 503
        )
    factory = Mock(return_value=analyzer)
    monkeypatch.setattr(main, "CaptureAnalyzer", factory)
    application = create_app()
    with TestClient(application) as http:
        assert http.get("/health").status_code == 200
        if enabled:
            analyzer.prepare.assert_awaited_once()
            analyzer.close.assert_not_called()
            assert application.state.admin_capture_analyzer is analyzer
        else:
            factory.assert_not_called()
    if enabled:
        analyzer.close.assert_called_once()
    database.close.assert_awaited_once()
    if failed:
        assert "photo capture remains available" in caplog.text


def test_initial_model_loading_does_not_consume_the_inference_budget(monkeypatch):
    class ColdConnection:
        def __init__(self):
            self.ready_at = time.monotonic() + 0.04
            self.sent = False

        def poll(self):
            return self.sent or time.monotonic() >= self.ready_at

        def recv(self):
            return (
                {"ok": {"candidates": [], "inference_ms": 1}}
                if self.sent
                else {"ready": True}
            )

        def send(self, _message):
            self.sent = True

    async def run():
        analyzer = CaptureAnalyzer(Path("unused"))

        def start():
            analyzer.process = FakeProcess()
            analyzer.connection = ColdConnection()

        monkeypatch.setattr(analyzer, "start", start)
        request = AsyncMock()
        request.is_disconnected.return_value = False
        result = await analyzer.infer(jpeg(), prompt(), request)
        assert result == {"candidates": [], "inference_ms": 1}

    monkeypatch.setattr(server_capture, "MODEL_START_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(server_capture, "ANALYSIS_TIMEOUT_SECONDS", 0.02)
    asyncio.run(run())
