"""Camera trial host: bounded ephemeral frame recognition, no database writes."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.live_camera.inference import CameraFailure, CameraRecognizer
from app.modules.live_camera.references import AtlasCameraReferences

STATIC = Path(__file__).with_name("static")
MAX_FRAME_BYTES = 256 * 1024


def create_camera_app(
    bundle: Path | None = None, recognizer: CameraRecognizer | None = None
) -> FastAPI:
    directory = bundle or Path(os.getenv("LIVE_CAMERA_BUNDLE", ".local/live-camera"))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        try:
            if bundle is None and recognizer is None:
                try:
                    settings = Settings()
                    if (
                        settings.live_camera_preload
                        and settings.live_camera_location_id
                        and settings.live_camera_object_ids
                    ):
                        task = asyncio.create_task(prepare())
                except Exception:
                    pass  # Readiness reports a safe failure when setup is incomplete.
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if application.state.owns_mongodb:
                await application.state.mongodb.close()
            application.state.mongodb = None
            application.state.references = None
            application.state.recognizer = None

    application = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    application.state.recognizer = recognizer
    application.state.references = None
    application.state.mongodb = None
    application.state.owns_mongodb = False
    init_gate = asyncio.Lock()
    gate = asyncio.Lock()
    last_scan = -1.0

    @application.exception_handler(CameraFailure)
    async def camera_error(request: Request, error: CameraFailure) -> Response:
        return JSONResponse({"error": error.code}, status_code=error.status)

    @application.middleware("http")
    async def camera_boundary(request: Request, call_next: object) -> Response:
        frame_route = request.url.path.rstrip("/").endswith("/recognize")
        if request.method not in {"GET", "HEAD"} and not (
            request.method == "POST" and frame_route
        ):
            response: Response = JSONResponse({"error": "READ_ONLY"}, status_code=405)
        else:
            response = await cast(RequestResponseEndpoint, call_next)(request)
        response.headers.update(
            {
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self'; "
                    "style-src 'self'; connect-src 'self'; worker-src 'none'; "
                    "img-src 'self'; media-src 'self' blob:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                    "form-action 'none'"
                ),
                "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            }
        )
        return response

    async def engine() -> CameraRecognizer:
        nonlocal directory
        async with init_gate:
            value = cast(CameraRecognizer | None, application.state.recognizer)
            if value is not None:
                return value
            # Explicit directory callers may replay old snapshots. The deployed
            # default always uses Atlas, even when a legacy catalog file exists.
            if bundle is not None:
                try:
                    value = await run_in_threadpool(CameraRecognizer, directory)
                except Exception:
                    raise CameraFailure("RECOGNITION_UNAVAILABLE") from None
                application.state.recognizer = value
                return value
            references = cast(
                AtlasCameraReferences | None, application.state.references
            )
            if references is None:
                try:
                    settings = Settings()
                    directory = settings.live_camera_bundle
                    if application.state.mongodb is None:
                        application.state.mongodb = MongoDB(settings)
                        application.state.owns_mongodb = True
                    references = AtlasCameraReferences(
                        application.state.mongodb.database, settings, directory
                    )
                    application.state.references = references
                except Exception:
                    raise CameraFailure("RECOGNITION_UNAVAILABLE") from None
        return await references.get()

    async def prepare() -> None:
        try:
            value = await engine()
            await run_in_threadpool(value.warm)
            application.state.preload_error = None
        except Exception:
            application.state.preload_error = "RECOGNITION_UNAVAILABLE"

    application.state.prepare = prepare
    application.state.preload_error = None

    @application.get("/ready")
    async def ready() -> Response:
        value = await engine()
        await run_in_threadpool(value.warm)
        return JSONResponse(
            {
                "status": "ready",
                "reference_version": value.reference_version,
                "automatic_identification": False,
            }
        )

    @application.get("/bundle/catalog.json")
    async def catalog() -> Response:
        value = await engine()
        await run_in_threadpool(value.warm)
        return JSONResponse(value.public_catalog())

    @application.get("/bundle/{filename:path}")
    async def bundle_file(filename: str) -> Response:
        # Model and reference descriptors stay on the server.
        if (
            filename in {"MODEL_LICENSE", "phone-ca.cer"}
            and (directory / filename).is_file()
        ):
            return FileResponse(directory / filename)
        return JSONResponse({"error": "NOT_FOUND"}, status_code=404)

    @application.post("/recognize")
    async def recognize(request: Request) -> Response:
        nonlocal last_scan
        if request.headers.get("content-type") != "image/jpeg":
            raise CameraFailure("JPEG_REQUIRED", 415)
        # Browsers cannot invoke this JPEG route across origins without preflight.
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise CameraFailure("SAME_ORIGIN_REQUIRED", 403)
        service = await engine()
        service.check_fresh()
        if request.query_params.get("location_id") != service.catalog["location"]["id"]:
            raise CameraFailure("WRONG_LOCATION", 409)
        if request.query_params.get("reference_version") != service.reference_version:
            raise CameraFailure("REFERENCES_CHANGED", 409)
        if gate.locked() or monotonic() - last_scan < 0.25:
            return JSONResponse(
                {"error": "RECOGNITION_BUSY"},
                status_code=429,
                headers={"Retry-After": "1"},
            )
        async with gate:
            body = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > MAX_FRAME_BYTES:
                            raise CameraFailure("FRAME_TOO_LARGE", 413)
                        body.extend(chunk)
                if not body:
                    raise CameraFailure("INVALID_FRAME", 422)
                current = await engine()
                if current.reference_version != service.reference_version:
                    raise CameraFailure("REFERENCES_CHANGED", 409)
                last_scan = monotonic()
                result = await run_in_threadpool(service.recognize, bytes(body))
                current = await engine()
                if current.reference_version != service.reference_version:
                    raise CameraFailure("REFERENCES_CHANGED", 409)
                return JSONResponse(result)
            except TimeoutError:
                raise CameraFailure("UPLOAD_TIMEOUT", 408) from None
            except CameraFailure:
                raise
            except Exception:
                raise CameraFailure("RECOGNITION_UNAVAILABLE") from None
            finally:
                body.clear()

    application.mount("/", StaticFiles(directory=STATIC, html=True), name="camera")
    return application


app = create_camera_app()
