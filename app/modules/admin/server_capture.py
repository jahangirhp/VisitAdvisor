"""Transient, selected-still analysis with bounded local CPU inference."""

import asyncio
import hashlib
import io
import logging
import multiprocessing
import time
import warnings
from collections import deque
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import Request
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.requests import ClientDisconnect

from app.core.exceptions import ApplicationError
from app.modules.admin.capture_inference import inference_worker
from app.modules.admin.server_model import MODEL_ID, MODEL_REVISION, MODEL_SHA256

MAX_ANALYSIS_BYTES = 1024 * 1024
MAX_ANALYSIS_EDGE = 1600
MAX_ANALYSIS_PIXELS = MAX_ANALYSIS_EDGE * MAX_ANALYSIS_EDGE
MAX_SESSION_CALLS = 12
MAX_SESSION_SECONDS = 300
MAX_SESSIONS = 32
MAX_STARTS_PER_MINUTE = 24
ANALYSIS_TIMEOUT_SECONDS = 20
MODEL_START_TIMEOUT_SECONDS = 45
BODY_TIMEOUT_SECONDS = 5
Normalized = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
CaptureId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")]
logger = logging.getLogger(__name__)


class AnalysisPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    session_id: CaptureId
    frame_id: CaptureId
    profile: Literal["planar", "multiview", "front-only", "scene"]
    point_x: Normalized
    point_y: Normalized
    box_x1: Normalized | None = None
    box_y1: Normalized | None = None
    box_x2: Normalized | None = None
    box_y2: Normalized | None = None

    @property
    def box(self) -> tuple[float, float, float, float] | None:
        values = self.box_x1, self.box_y1, self.box_x2, self.box_y2
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise ValueError("Provide the complete target box.")
        return tuple(float(value) for value in values if value is not None)  # type: ignore[return-value]

    @model_validator(mode="after")
    def validate_box(self) -> Self:
        box = self.box
        if box is not None:
            x1, y1, x2, y2 = box
            if not (x1 < x2 and y1 < y2):
                raise ValueError("Target box must have positive area.")
            if not (x1 <= self.point_x <= x2 and y1 <= self.point_y <= y2):
                raise ValueError("Select a point inside the target box.")
        return self


class MaskCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(pattern=r"^mask-[0-2]$")
    score: float
    target_box: tuple[Normalized, Normalized, Normalized, Normalized]
    mask_png_base64: str = Field(max_length=700_000)
    mask_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InferenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    candidates: list[MaskCandidate] = Field(max_length=3)
    inference_ms: float = Field(ge=0)


class AnalysisResult(InferenceResult):
    version: Literal["server-capture-v1"] = "server-capture-v1"
    session_id: str
    frame_id: str
    source_sha256: str
    width: int
    height: int
    model: dict[str, str]


@dataclass
class AnalysisSession:
    started: float
    calls: int = 0
    byte_count: int = 0


def failure(code: str, message: str, status: int) -> ApplicationError:
    return ApplicationError(f"CAPTURE_ANALYSIS_{code}", message, status)


async def read_frame(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0] != "image/jpeg":
        raise failure("IMAGE_TYPE", "Use a JPEG still image.", 415)
    received = bytearray()
    try:
        async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                if len(received) + len(chunk) > MAX_ANALYSIS_BYTES:
                    raise failure("TOO_LARGE", "Analysis photo exceeds 1 MiB.", 413)
                received.extend(chunk)
    except TimeoutError:
        raise failure(
            "TIMEOUT", "Photo upload timed out; retry capture.", 408
        ) from None
    except ClientDisconnect:
        raise failure("CANCELLED", "Photo analysis was cancelled.", 499) from None
    return bytes(received)


def validate_frame(data: bytes) -> tuple[int, int]:
    if not data:
        raise failure("INVALID_IMAGE", "Use a valid JPEG still image.", 422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "JPEG":
                    raise failure("IMAGE_TYPE", "Use a JPEG still image.", 415)
                if (
                    max(image.size) > MAX_ANALYSIS_EDGE
                    or image.width * image.height > MAX_ANALYSIS_PIXELS
                ):
                    raise failure(
                        "TOO_LARGE", "Use a photo up to 1600 pixels wide.", 413
                    )
                if image.getexif().get(274, 1) != 1:
                    raise failure(
                        "ORIENTATION", "Orient the photo before analysis.", 422
                    )
                image.verify()
                return image.size
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise failure("TOO_LARGE", "Photo dimensions are too large.", 413) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise failure("INVALID_IMAGE", "Use a valid JPEG still image.", 422) from None


class CaptureAnalyzer:
    """One child and no queue per API process; sessions retain counters only."""

    def __init__(self, model_directory: Path) -> None:
        self.model_directory = model_directory
        self.lock = asyncio.Lock()
        self.sessions: dict[str, AnalysisSession] = {}
        self.starts: deque[float] = deque()
        self.process: BaseProcess | None = None
        self.connection: Connection | None = None
        self.ready = False

    def reserve(self, session_id: str, byte_count: int) -> None:
        now = time.monotonic()
        self.sessions = {
            key: value
            for key, value in self.sessions.items()
            if now - value.started < MAX_SESSION_SECONDS
        }
        while self.starts and now - self.starts[0] >= 60:
            self.starts.popleft()
        if len(self.starts) >= MAX_STARTS_PER_MINUTE:
            raise failure("LIMIT", "Analysis is busy; retry in a minute.", 429)
        current = self.sessions.get(session_id)
        if current is None:
            if len(self.sessions) >= MAX_SESSIONS:
                raise failure(
                    "LIMIT", "Analysis is busy; use a photo or retry later.", 429
                )
            current = AnalysisSession(now)
            self.sessions[session_id] = current
        if (
            current.calls >= MAX_SESSION_CALLS
            or current.byte_count + byte_count > MAX_SESSION_CALLS * MAX_ANALYSIS_BYTES
        ):
            raise failure("LIMIT", "Session analysis limit reached; use a photo.", 429)
        current.calls += 1
        current.byte_count += byte_count
        self.starts.append(now)

    def start(self) -> None:
        if self.process is not None and self.process.is_alive():
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=inference_worker,
            args=(child, self.model_directory),
            daemon=True,
        )
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            process.close()
            raise
        child.close()
        self.process = process
        self.connection = parent

    def close(self) -> None:
        self.ready = False
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=0.2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.2)
            self.process.close()
            self.process = None

    async def prepare(self) -> None:
        """Warm one resident child without uploading or retaining a source frame."""
        async with self.lock:
            try:
                await self.ensure_ready()
            except (ApplicationError, asyncio.CancelledError):
                self.close()
                raise
            except TimeoutError:
                self.close()
                raise failure("TIMEOUT", "Analysis startup timed out.", 504) from None
            except Exception:
                self.close()
                raise failure(
                    "UNAVAILABLE", "Photo analysis is unavailable.", 503
                ) from None

    async def ensure_ready(self, request: Request | None = None) -> bool:
        if self.ready and self.process is not None and self.process.is_alive():
            return False
        logger.info("Capture model starting")
        async with asyncio.timeout(MODEL_START_TIMEOUT_SECONDS):
            self.start()
            assert self.connection is not None
            disconnected = await self.wait_for_message(request)
            ready = await asyncio.to_thread(self.connection.recv)
            if ready != {"ready": True}:
                raise failure("UNAVAILABLE", "Use a photo or retry analysis.", 503)
        self.ready = True
        logger.info("Capture model ready")
        return disconnected

    async def infer(
        self, data: bytes, prompt: AnalysisPrompt, request: Request
    ) -> dict[str, Any]:
        try:
            if await self.ensure_ready(request):
                # Finish bounded readiness after navigation, but do not start
                # image inference for a client that already left.
                raise failure("CANCELLED", "Photo analysis was cancelled.", 499)
            assert self.connection is not None
            async with asyncio.timeout(ANALYSIS_TIMEOUT_SECONDS):
                await asyncio.to_thread(
                    self.connection.send,
                    {
                        "image": data,
                        "point": [prompt.point_x, prompt.point_y],
                        "box": prompt.box,
                    },
                )
                disconnected = await self.wait_for_message(request)
                result = await asyncio.to_thread(self.connection.recv)
            if not isinstance(result, dict) or set(result) != {"ok"}:
                raise failure("UNAVAILABLE", "Use a photo or retry analysis.", 503)
            validated = InferenceResult.model_validate(result["ok"])
            if disconnected:
                # Consume and discard the selected still's valid reply while
                # holding the analyzer lock. Keeping the ready weights avoids
                # making the next object pay for another cold model import.
                raise failure("CANCELLED", "Photo analysis was cancelled.", 499)
            return validated.model_dump()
        except TimeoutError:
            self.close()
            raise failure(
                "TIMEOUT", "Analysis timed out; use a photo or retry.", 504
            ) from None
        except ApplicationError as exc:
            if exc.code != "CAPTURE_ANALYSIS_CANCELLED":
                self.close()
            raise
        except asyncio.CancelledError:
            self.close()
            raise
        except Exception:
            self.close()
            raise failure(
                "UNAVAILABLE", "Use a photo or retry analysis.", 503
            ) from None

    async def wait_for_message(self, request: Request | None = None) -> bool:
        """Drain a selected operation within the caller's existing deadline."""
        assert self.connection is not None
        disconnected = False
        while True:
            if not disconnected and request is not None:
                disconnected = await request.is_disconnected()
            if self.connection.poll():
                return disconnected
            if self.process is None or not self.process.is_alive():
                raise failure("UNAVAILABLE", "Use a photo or retry analysis.", 503)
            await asyncio.sleep(0.03)

    async def analyze(self, prompt: AnalysisPrompt, request: Request) -> AnalysisResult:
        if self.lock.locked():
            raise failure("BUSY", "Another photo is being checked; retry shortly.", 429)
        async with self.lock:
            data = await read_frame(request)
            width, height = await asyncio.to_thread(validate_frame, data)
            self.reserve(prompt.session_id, len(data))
            result = await self.infer(data, prompt, request)
            return AnalysisResult(
                session_id=prompt.session_id,
                frame_id=prompt.frame_id,
                source_sha256=hashlib.sha256(data).hexdigest(),
                width=width,
                height=height,
                model={
                    "name": MODEL_ID,
                    "revision": MODEL_REVISION,
                    "sha256": MODEL_SHA256,
                },
                **result,
            )
