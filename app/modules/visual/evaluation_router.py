"""Dev-only page and API to run visual evaluations and keep their history."""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Body, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import get_database
from app.modules.visual.evaluation import (
    MAX_UPLOAD_BYTES,
    EvaluationError,
    build_references,
    create_series,
    delete_path,
    library,
    list_datasets,
    load_dataset,
    resolve,
    run_dataset,
    save_image,
    summarize,
)

logger = logging.getLogger(__name__)
COLLECTION = "visual_evaluation_runs"
PAGE = Path(__file__).parent / "static" / "evaluation.html"


def enabled(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None or not settings.visual_evaluation_enabled:
        raise ApplicationError("NOT_FOUND", "Not found.", 404)
    if "test" not in settings.mongodb_database.lower():
        raise ApplicationError(
            "NOT_A_TEST_DATABASE", "MONGODB_DATABASE must contain 'test'.", 403
        )
    return settings


router = APIRouter(
    prefix="/visual-evaluation",
    tags=["visual-evaluation"],
    dependencies=[Depends(enabled)],
)
Config = Annotated[Settings, Depends(enabled)]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str
    threshold: float = Field(default=0.45, ge=0, le=1)


def running(request: Request) -> bool:
    task: asyncio.Task[None] | None = getattr(
        request.app.state, "evaluation_task", None
    )
    return task is not None and not task.done()


def current(request: Request, run: str) -> bool:
    progress: dict[str, Any] | None = getattr(
        request.app.state, "evaluation_progress", None
    )
    return running(request) and progress is not None and progress["id"] == run


def public(doc: dict[str, Any], *, details: bool, alive: bool) -> dict[str, Any]:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    if doc["status"] == "running" and not alive:
        out["status"], out["error"] = "failed", "INTERRUPTED"
    if not details:
        out.pop("results", None)
    return out


def run_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId:
        raise ApplicationError("RUN_NOT_FOUND", "Run not found.", 404) from None


def dataset_error(exc: EvaluationError) -> ApplicationError:
    status = 404 if str(exc) == "DATASET_NOT_FOUND" else 422
    return ApplicationError(str(exc), "Dataset cannot be used.", status)


@router.get("", include_in_schema=False)
async def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


def library_error(exc: EvaluationError) -> ApplicationError:
    status = 404 if str(exc) in ("DATASET_NOT_FOUND",) else 422
    return ApplicationError(str(exc), "Request cannot be used.", status)


@router.get("/datasets")
async def datasets(settings: Config) -> list[dict[str, Any]]:
    rows = []
    for name in list_datasets(settings.visual_evaluation_root):
        try:
            count = len(load_dataset(settings.visual_evaluation_root, name))
        except EvaluationError:
            continue
        rows.append({"name": name, "photos": count})
    return rows


@router.get("/library")
async def get_library(settings: Config) -> dict[str, Any]:
    return library(settings.visual_evaluation_root)


@router.get("/library/image")
async def library_image(path: str, settings: Config) -> FileResponse:
    try:
        target = resolve(settings.visual_evaluation_root, path)
    except EvaluationError as exc:
        raise library_error(exc) from None
    if not target.is_file():
        raise ApplicationError("PHOTO_NOT_FOUND", "Photo not found.", 404)
    return FileResponse(target)


class SeriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


@router.post("/library/series", status_code=201)
async def add_series(body: SeriesRequest, settings: Config) -> dict[str, str]:
    try:
        create_series(settings.visual_evaluation_root, body.name)
    except EvaluationError as exc:
        raise library_error(exc) from None
    return {"name": body.name}


@router.post("/library/upload", status_code=201)
async def upload(
    settings: Config,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "other",
    category: Annotated[str, Form()] = "full",
    series: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        saved = save_image(
            settings.visual_evaluation_root,
            series=series or None,
            kind=kind,
            filename=file.filename or "image.jpg",
            category=category,
            data=data,
        )
    except EvaluationError as exc:
        raise library_error(exc) from None
    return {"saved": saved}


@router.delete("/library")
async def delete_library(path: str, settings: Config) -> dict[str, str]:
    try:
        delete_path(settings.visual_evaluation_root, path)
    except EvaluationError as exc:
        raise library_error(exc) from None
    return {"deleted": path}


def building(request: Request) -> bool:
    task: asyncio.Task[None] | None = getattr(request.app.state, "build_task", None)
    return task is not None and not task.done()


@router.get("/build")
async def build_status(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {
        "running": building(request),
        **getattr(state, "build_status", {"state": "idle"}),
    }


@router.post("/build", status_code=202)
async def start_build(request: Request, settings: Config) -> dict[str, str]:
    state = request.app.state
    encoder = getattr(state, "visual_encoder", None)
    if encoder is None:
        raise ApplicationError(
            "VISUAL_NOT_CONFIGURED", "Visual recognition is not enabled.", 503
        )
    if building(request) or running(request):
        raise ApplicationError("BUSY", "A build or evaluation is running.", 409)
    database = get_database(request)
    state.build_status = {"state": "running", "done": 0, "total": 0}

    def progress(done: int, total: int) -> None:
        state.build_status = {"state": "running", "done": done, "total": total}

    async def work() -> None:
        try:
            report = await build_references(
                settings.visual_evaluation_root,
                database,
                encoder,
                state.visual_lock,
                progress,
            )
            state.build_status = {"state": "done", "report": report}
        except Exception as exc:
            logger.exception("Reference build failed")
            code = exc.code if isinstance(exc, ApplicationError) else "BUILD_FAILED"
            state.build_status = {"state": "failed", "error": code}

    state.build_task = asyncio.create_task(work())
    return {"state": "running"}


@router.get("/runs")
async def runs(request: Request) -> list[dict[str, Any]]:
    cursor = get_database(request)[COLLECTION].find({}, {"results": 0})
    docs = await cursor.sort("started_at", -1).limit(200).to_list()
    return [
        public(d, details=False, alive=current(request, str(d["_id"]))) for d in docs
    ]


@router.get("/runs/{run}")
async def get_run(run: str, request: Request) -> dict[str, Any]:
    doc = await get_database(request)[COLLECTION].find_one({"_id": run_id(run)})
    if doc is None:
        raise ApplicationError("RUN_NOT_FOUND", "Run not found.", 404)
    out = public(doc, details=True, alive=current(request, run))
    if out["status"] == "running":
        out["done"] = request.app.state.evaluation_progress["done"]
    return out


@router.delete("/runs")
async def clear_runs(request: Request) -> dict[str, int]:
    if running(request):
        raise ApplicationError("RUN_IN_PROGRESS", "An evaluation is running.", 409)
    deleted = await get_database(request)[COLLECTION].delete_many({})
    return {"deleted": deleted.deleted_count}


@router.delete("/runs/{run}")
async def delete_run(run: str, request: Request) -> dict[str, int]:
    if current(request, run):
        raise ApplicationError("RUN_IN_PROGRESS", "This evaluation is running.", 409)
    deleted = await get_database(request)[COLLECTION].delete_one({"_id": run_id(run)})
    if not deleted.deleted_count:
        raise ApplicationError("RUN_NOT_FOUND", "Run not found.", 404)
    return {"deleted": deleted.deleted_count}


@router.post("/runs", status_code=202)
async def start_run(
    request: Request, body: Annotated[RunRequest, Body()], settings: Config
) -> dict[str, str]:
    state = request.app.state
    encoder = getattr(state, "visual_encoder", None)
    if encoder is None:
        raise ApplicationError(
            "VISUAL_NOT_CONFIGURED", "Visual recognition is not enabled.", 503
        )
    if running(request) or building(request):
        raise ApplicationError("RUN_IN_PROGRESS", "An evaluation is running.", 409)
    root = settings.visual_evaluation_root
    try:
        photos = load_dataset(root, body.dataset)
    except EvaluationError as exc:
        raise dataset_error(exc) from None

    database = get_database(request)
    inserted = await database[COLLECTION].insert_one(
        {
            "dataset": body.dataset,
            "threshold": body.threshold,
            "status": "running",
            "started_at": datetime.now(UTC),
            "finished_at": None,
            "model": encoder.model,
            "preprocessing": encoder.preprocessing,
            "total": len(photos),
            "error": None,
        }
    )
    run = str(inserted.inserted_id)
    state.evaluation_progress = {"id": run, "done": 0}

    def progress(done: int) -> None:
        state.evaluation_progress = {"id": run, "done": done}

    async def work() -> None:
        changes: dict[str, Any]
        try:
            results = await run_dataset(
                root,
                photos,
                database,
                encoder,
                state.visual_lock,
                progress,
                body.threshold,
            )
            changes = {"status": "done", "results": results, **summarize(results)}
        except (EvaluationError, ApplicationError) as exc:
            code = exc.code if isinstance(exc, ApplicationError) else str(exc)
            changes = {"status": "failed", "error": code}
        except Exception:
            logger.exception("Visual evaluation failed")
            changes = {"status": "failed", "error": "EVALUATION_FAILED"}
        changes["finished_at"] = datetime.now(UTC)
        try:
            await database[COLLECTION].update_one(
                {"_id": inserted.inserted_id}, {"$set": changes}
            )
        except Exception:
            logger.error("Could not save visual evaluation result")

    state.evaluation_task = asyncio.create_task(work())
    return {"id": run}
