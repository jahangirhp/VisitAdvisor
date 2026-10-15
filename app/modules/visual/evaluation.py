"""Positive-photo evaluation of live visual recognition, shared by the UI and tests.

A dataset is `<root>/datasets/<name>.json`: a list of photos whose paths are
relative to the root. Every photo shows an object that has Atlas references.
"""

import asyncio
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pymongo.asynchronous.database import AsyncDatabase

from app.core.domain import validate_id
from app.modules.visual.features import ImageEncoder
from app.modules.visual.repository import VisualRepository
from app.modules.visual.service import VisualService

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_PHOTOS = 500


class EvaluationError(Exception):
    """Carries a public error code only."""


class EvalPhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    path: str = Field(min_length=1)
    location_id: str
    expected_object_id: str
    category: Literal["full", "partial"]
    object_type: str = Field(pattern=r"^[a-z][a-z ()-]{0,39}$")


def datasets_dir(root: Path) -> Path:
    return root / "datasets"


def list_datasets(root: Path) -> list[str]:
    directory = datasets_dir(root)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if NAME.match(p.stem))


def load_dataset(root: Path, name: str) -> list[EvalPhoto]:
    if not NAME.match(name) or name not in list_datasets(root):
        raise EvaluationError("DATASET_NOT_FOUND")
    try:
        photos = TypeAdapter(list[EvalPhoto]).validate_json(
            (datasets_dir(root) / f"{name}.json").read_bytes()
        )
    except ValueError:
        raise EvaluationError("DATASET_INVALID") from None
    if not 1 <= len(photos) <= MAX_PHOTOS or len({p.id for p in photos}) != len(photos):
        raise EvaluationError("DATASET_INVALID")
    for photo in photos:
        validate_id(photo.location_id)
        validate_id(photo.expected_object_id)
        photo_path(root, photo)
    return photos


def photo_path(root: Path, photo: EvalPhoto) -> Path:
    resolved = (root / photo.path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise EvaluationError("DATASET_PHOTO_MISSING")
    return resolved


def judge(photo: EvalPhoto, status: str, candidates: list[dict[str, Any]]) -> dict:
    ids = [c["object_id"] for c in candidates]
    rank = (
        ids.index(photo.expected_object_id) + 1
        if photo.expected_object_id in ids
        else None
    )
    return {
        "id": photo.id,
        "path": photo.path,
        "category": photo.category,
        "object_type": photo.object_type,
        "expected_object_id": photo.expected_object_id,
        "status": status,
        "rank": rank,
        "top1": rank == 1,
        "top3": rank is not None and rank <= 3,
        "candidates": [
            {
                "object_id": c["object_id"],
                "name": c["name"],
                "score": round(c["score"], 4),
            }
            for c in candidates[:5]
        ],
    }


def summarize(results: list[dict]) -> dict[str, Any]:
    def block(rows: list[dict]) -> dict[str, int]:
        return {
            "count": len(rows),
            "top1": sum(r["top1"] for r in rows),
            "top3": sum(r["top3"] for r in rows),
        }

    def group(key: str) -> dict[str, dict[str, int]]:
        return {
            value: block([r for r in results if r[key] == value])
            for value in sorted({r[key] for r in results})
        }

    return {
        **block(results),
        "by_category": group("category"),
        "by_type": group("object_type"),
    }


async def run_dataset(
    root: Path,
    photos: list[EvalPhoto],
    database: AsyncDatabase[dict[str, Any]],
    encoder: ImageEncoder,
    lock: asyncio.Lock,
    progress: Callable[[int], None] | None = None,
) -> list[dict]:
    service = VisualService(VisualRepository(database), encoder, lock)
    results = []
    for done, photo in enumerate(photos, 1):
        path = photo_path(root, photo)
        response = await service.recognize(
            photo.location_id,
            await asyncio.to_thread(path.read_bytes),
            mimetypes.guess_type(path.name)[0],
        )
        results.append(
            judge(
                photo,
                response.status,
                [c.model_dump() for c in response.candidates],
            )
        )
        if progress:
            progress(done)
    return results
