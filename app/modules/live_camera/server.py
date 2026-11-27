"""Camera trial host: bounded ephemeral frame recognition, no database writes."""

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import cast

from bson import ObjectId
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.live_camera.experience import read_experience
from app.modules.live_camera.inference import CameraFailure, CameraRecognizer
from app.modules.live_camera.references import AtlasCameraReferences
from app.modules.live_camera.venue_package import PackageError, build_package

STATIC = Path(__file__).with_name("static")
MAX_FRAME_BYTES = 256 * 1024


def create_camera_app(
    bundle: Path | None = None,
    recognizer: CameraRecognizer | None = None,
    configured_settings: Settings | None = None,
) -> FastAPI:
    directory = bundle or Path(os.getenv("LIVE_CAMERA_BUNDLE", ".local/live-camera"))

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None = None
        try:
            if bundle is None and recognizer is None:
                try:
                    settings = configured_settings or Settings()
                    if (
                        settings.live_camera_preload
                        and settings.live_camera_location_id
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
            application.state.venue_references = None
            application.state.recognizer = None

    application = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    application.state.recognizer = recognizer
    application.state.references = None
    application.state.venue_references = None
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

    async def engine(location_id: str | None = None) -> CameraRecognizer:
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
            if location_id:
                settings = configured_settings or Settings()
                if not ObjectId.is_valid(location_id):
                    raise CameraFailure("WRONG_LOCATION", 409)
                current_references = application.state.references
                if location_id != settings.live_camera_location_id and (
                    current_references is None
                    or location_id != current_references.location_id
                ):
                    if application.state.mongodb is None:
                        application.state.mongodb = MongoDB(settings)
                        application.state.owns_mongodb = True
                    selected = application.state.venue_references
                    if selected is None or selected.location_id != location_id:
                        venue = await application.state.mongodb.database[
                            "locations"
                        ].find_one(
                            {"_id": ObjectId(location_id), "status": "active"},
                            {"_id": 1},
                        )
                        if venue is None:
                            raise CameraFailure("WRONG_LOCATION", 409)
                        selected = AtlasCameraReferences(
                            application.state.mongodb.database,
                            settings.model_copy(
                                update={
                                    "live_camera_location_id": location_id,
                                    "live_camera_object_ids": [],
                                    "live_camera_calibration": None,
                                }
                            ),
                            settings.live_camera_bundle,
                            discover_objects=True,
                        )
                        previous = (
                            application.state.venue_references
                            or application.state.references
                        )
                        if previous is not None:
                            selected.session = previous.session
                        application.state.venue_references = selected
                    references = selected
                else:
                    references = None
            else:
                references = None
            if references is None:
                references = cast(
                    AtlasCameraReferences | None, application.state.references
                )
                if references is None:
                    try:
                        settings = configured_settings or Settings()
                        directory = settings.live_camera_bundle
                        if application.state.mongodb is None:
                            application.state.mongodb = MongoDB(settings)
                            application.state.owns_mongodb = True
                        references = AtlasCameraReferences(
                            application.state.mongodb.database,
                            settings,
                            directory,
                            discover_objects=True,
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

    @application.get("/venues")
    async def venues(offset: int = Query(default=0, ge=0)) -> Response:
        if bundle is not None or recognizer is not None:
            value = await engine()
            return JSONResponse([value.catalog["location"]] if offset == 0 else [])
        try:
            async with init_gate:
                if application.state.mongodb is None:
                    application.state.mongodb = MongoDB(
                        configured_settings or Settings()
                    )
                    application.state.owns_mongodb = True
            rows = await (
                application.state.mongodb.database["locations"]
                .find({"status": "active"}, {"name": 1, "slug": 1})
                .sort("_id", 1)
                .skip(offset)
                .limit(100)
                .to_list(length=100)
            )
            return JSONResponse(
                [
                    {
                        "id": str(row["_id"]),
                        "name": row["name"],
                        "slug": row.get("slug"),
                    }
                    for row in rows
                ]
            )
        except Exception:
            raise CameraFailure("VENUES_UNAVAILABLE") from None

    @application.get("/ready")
    async def ready(location_id: str | None = None) -> Response:
        value = await engine(location_id)
        await run_in_threadpool(value.warm)
        return JSONResponse(
            {
                "status": "ready",
                "reference_version": value.reference_version,
                "automatic_identification": False,
            }
        )

    @application.get("/bundle/catalog.json")
    async def catalog(location_id: str | None = None) -> Response:
        value = await engine(location_id)
        await run_in_threadpool(value.warm)
        return JSONResponse(value.public_catalog())

    @application.get("/objects/{object_id}/experience")
    async def experience(object_id: str, request: Request) -> Response:
        service = await engine(request.query_params.get("location_id"))
        service.check_fresh()
        location_id = service.catalog["location"]["id"]
        if request.query_params.get("location_id") != location_id:
            raise CameraFailure("WRONG_LOCATION", 409)
        version = service.reference_version
        if request.query_params.get("reference_version") != version:
            raise CameraFailure("REFERENCES_CHANGED", 409)
        obj = next(
            (o for o in service.catalog.get("objects", []) if o["id"] == object_id),
            None,
        )
        if obj is None:
            raise CameraFailure("OBJECT_NOT_FOUND", 404)
        # Historical file replays have no canonical database/content fallback.
        database = (
            application.state.mongodb.database
            if application.state.mongodb is not None
            else None
        )
        if database is None:
            raise CameraFailure("CONTENT_UNAVAILABLE", 503)
        try:
            async with asyncio.timeout(8):
                prepared = await read_experience(
                    database,
                    object_id,
                    location_id,
                    {s["id"] for s in obj["sources"]},
                )
                current = await engine(request.query_params.get("location_id"))
                current.check_fresh()
                if current.reference_version != version:
                    raise CameraFailure("REFERENCES_CHANGED", 409)
        except CameraFailure:
            raise
        except Exception:
            raise CameraFailure("CONTENT_UNAVAILABLE", 503) from None
        return JSONResponse(
            {
                "object_id": object_id,
                "location_id": location_id,
                "reference_version": version,
                "content": prepared.content.model_dump(mode="json"),
                "source_ids": prepared.source_ids,
                "review_status": prepared.review_status,
            }
        )

    def package_root(location_id: str) -> Path:
        settings = configured_settings or Settings()
        if not settings.live_camera_offline_packages:
            # Reference descriptors stay on the server unless explicitly enabled.
            raise CameraFailure("NOT_FOUND", 404)
        return settings.live_camera_package_cache / location_id

    @application.get("/venues/{location_id}/package/manifest")
    async def package_manifest(location_id: str, request: Request) -> Response:
        """Offline iOS package for the exact snapshot this server recognizes against."""
        root = package_root(location_id)
        if not ObjectId.is_valid(location_id):
            raise CameraFailure("INVALID_LOCATION", 422)
        value = await engine(location_id)
        if value.catalog["location"]["id"] != location_id:
            raise CameraFailure("REFERENCES_CHANGED", 409)
        version = value.reference_version
        etag = f'"{version}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        try:
            manifest = await run_in_threadpool(
                build_package, value.catalog, root / version
            )
        except PackageError:
            raise CameraFailure("PACKAGE_UNAVAILABLE", 409) from None
        except Exception:
            raise CameraFailure("PACKAGE_UNAVAILABLE") from None
        return JSONResponse(manifest, headers={"ETag": etag})

    @application.get("/venues/{location_id}/package/{version}/{asset:path}")
    async def package_asset(location_id: str, version: str, asset: str) -> Response:
        if not ObjectId.is_valid(location_id) or not re.fullmatch(
            r"[0-9a-f]{64}", version
        ):
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        root = package_root(location_id) / version
        try:
            manifest = json.loads((root / "manifest.json").read_text())
        except (OSError, ValueError):
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        # Only files listed in the immutable manifest are served; no path input
        # reaches the filesystem without matching an exact manifest entry.
        if asset != "manifest.json" and asset not in {
            entry["path"] for entry in manifest["files"]
        }:
            return JSONResponse({"error": "NOT_FOUND"}, status_code=404)
        return FileResponse(root / asset, headers={"ETag": f'"{version}"'})

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
        service = await engine(request.query_params.get("location_id"))
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
                current = await engine(request.query_params.get("location_id"))
                if current.reference_version != service.reference_version:
                    raise CameraFailure("REFERENCES_CHANGED", 409)
                last_scan = monotonic()
                result = await run_in_threadpool(service.recognize, bytes(body))
                current = await engine(request.query_params.get("location_id"))
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
