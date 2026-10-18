"""Folder-based evaluation of live visual recognition, shared by the UI and tests.

Layout under the data root:
  references/<type>/<name>.jpg     one image = one test object (embedded into the DB)
  positive/<series>/<photo>.jpg    test photos; matched to a reference by file name
A photo `<name>.jpg`, `<name>-2.jpg` or `<name>.partial.jpg` expects reference
`<name>` (longest name wins). A photo with no such reference passes when any
reference scores at least the run threshold. `.partial` marks a partial view.
"""

import asyncio
import hashlib
import mimetypes
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field
from pymongo.asynchronous.database import AsyncDatabase

from app.core.exceptions import ApplicationError
from app.modules.recognition.images import prepare_image
from app.modules.visual.features import ImageEncoder, digest
from app.modules.visual.repository import (
    VisualRepository,
    evidence_hash,
)
from app.modules.visual.service import VisualService

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TYPES = ("painting", "statue", "sculpture", "object", "other")
EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
MAX_PHOTOS = 500
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_UPLOAD_PIXELS = 100_000_000
LOCATION_SLUG = "visual-test"


class EvaluationError(Exception):
    """Carries a public error code only."""


class EvalPhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1)
    path: str = Field(min_length=1)
    object: str | None
    category: Literal["full", "partial"]
    object_type: str


def category_of(filename: str) -> Literal["full", "partial"]:
    return "partial" if Path(filename).stem.endswith(".partial") else "full"


def check_name(value: str) -> str:
    if not NAME.match(value):
        raise EvaluationError("INVALID_NAME")
    return value


def images_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.suffix.lower() in EXTENSIONS and p.is_file()
    )


def subdirs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_dir() and NAME.match(p.name))


def references(root: Path) -> list[tuple[str, str, Path]]:
    """Return (object name, type, image path) for every reference image."""
    return sorted(
        (image.stem, kind, image)
        for kind in TYPES
        for image in images_in(root / "references" / kind)
    )


def expected_object(filename: str, names: list[str]) -> str | None:
    stem = Path(filename).stem.removesuffix(".partial")
    matches = [n for n in names if stem == n or stem.startswith((n + "-", n + "."))]
    return max(matches, key=len) if matches else None


def resolve(root: Path, relative: str) -> Path:
    """Resolve a path under references/ or positive/, refusing anything else."""
    path = (root / relative).resolve()
    base = root.resolve()
    parts = path.relative_to(base).parts if path.is_relative_to(base) else ()
    if len(parts) < 2 or parts[0] not in ("references", "positive"):
        raise EvaluationError("INVALID_PATH")
    return path


def library(root: Path) -> dict[str, Any]:
    refs = references(root)
    names = [name for name, _, _ in refs]
    return {
        "types": list(TYPES),
        "references": [
            {
                "name": name,
                "type": kind,
                "path": image.relative_to(root).as_posix(),
            }
            for name, kind, image in refs
        ],
        "series": [
            {
                "name": series.name,
                "photos": [
                    {
                        "name": p.name,
                        "path": p.relative_to(root).as_posix(),
                        "category": category_of(p.name),
                        "object": expected_object(p.name, names),
                    }
                    for p in images_in(series)
                ],
            }
            for series in subdirs(root / "positive")
        ],
    }


def create_series(root: Path, name: str) -> None:
    (root / "positive" / check_name(name)).mkdir(parents=True, exist_ok=True)


def save_image(
    root: Path,
    *,
    series: str | None,
    kind: str,
    filename: str,
    category: str,
    data: bytes,
) -> str:
    """Validate and store one uploaded image; returns its path relative to root."""
    if series is None:
        if kind not in TYPES:
            raise EvaluationError("INVALID_TYPE")
        folder = root / "references" / kind
    else:
        folder = root / "positive" / check_name(series)
        if not folder.is_dir():
            raise EvaluationError("DATASET_NOT_FOUND")
    extension = Path(filename).suffix.lower()
    if extension not in EXTENSIONS or category not in ("full", "partial"):
        raise EvaluationError("UNSUPPORTED_IMAGE")
    try:
        # Store the same normalized JPEG (max 2560 px) the app embeds, so large
        # originals also stay under the recognition upload limit.
        data = prepare_image(
            data,
            EXTENSIONS[extension],
            max_bytes=MAX_UPLOAD_BYTES,
            max_pixels=MAX_UPLOAD_PIXELS,
        )
    except ApplicationError as exc:
        raise EvaluationError(exc.code) from None
    extension = ".jpg"
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.lower()).strip("-")[:48]
    stem = (stem or "image").removesuffix("-partial")
    suffix = ".partial" if series is not None and category == "partial" else ""
    taken = {name for name, _, _ in references(root)}
    candidate, counter = stem, 2
    while (series is None and candidate in taken) or (
        folder / f"{candidate}{suffix}{extension}"
    ).exists():
        candidate = f"{stem}-{counter}"
        counter += 1
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{candidate}{suffix}{extension}"
    target.write_bytes(data)
    return target.relative_to(root).as_posix()


def delete_path(root: Path, relative: str) -> None:
    path = resolve(root, relative)
    parts = path.relative_to(root.resolve()).parts
    if path.is_file() and path.suffix.lower() in EXTENSIONS:
        path.unlink()
    elif path.is_dir() and parts[0] == "positive" and len(parts) == 2:
        shutil.rmtree(path)
    else:
        raise EvaluationError("INVALID_PATH")


def list_datasets(root: Path) -> list[str]:
    return [s.name for s in subdirs(root / "positive") if images_in(s)]


def load_dataset(root: Path, name: str) -> list[EvalPhoto]:
    series = root / "positive" / name
    if not NAME.match(name) or not series.is_dir():
        raise EvaluationError("DATASET_NOT_FOUND")
    refs = {n: kind for n, kind, _ in references(root)}
    photos: list[EvalPhoto] = []
    for image in images_in(series):
        expected = expected_object(image.name, list(refs))
        photos.append(
            EvalPhoto(
                id=len(photos) + 1,
                path=image.relative_to(root).as_posix(),
                object=expected,
                category=category_of(image.name),
                object_type=refs[expected] if expected else "unknown",
            )
        )
    if not 1 <= len(photos) <= MAX_PHOTOS:
        raise EvaluationError("DATASET_INVALID")
    return photos


def photo_path(root: Path, photo: EvalPhoto) -> Path:
    path = resolve(root, photo.path)
    if not path.is_file():
        raise EvaluationError("DATASET_PHOTO_MISSING")
    return path


def judge(
    photo: EvalPhoto,
    expected_object_id: str | None,
    status: str,
    candidates: list[dict[str, Any]],
    threshold: float = 0.45,
) -> dict[str, Any]:
    ids = [c["object_id"] for c in candidates]
    rank = ids.index(expected_object_id) + 1 if expected_object_id in ids else None
    if expected_object_id is None:
        # No named reference: success means any database object matches well enough.
        found = any(c["score"] >= threshold for c in candidates)
        top1 = top3 = found
    else:
        top1, top3 = rank == 1, rank is not None and rank <= 3
    return {
        "id": photo.id,
        "path": photo.path,
        "object": photo.object,
        "category": photo.category,
        "object_type": photo.object_type,
        "expected_object_id": expected_object_id,
        "status": status,
        "rank": rank,
        "named": expected_object_id is not None,
        "top1": top1,
        "top3": top3,
        "candidates": [
            {
                "object_id": c["object_id"],
                "name": c["name"],
                "score": round(c["score"], 4),
            }
            for c in candidates[:5]
        ],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def block(rows: list[dict[str, Any]]) -> dict[str, int]:
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


async def ensure_location(database: AsyncDatabase[dict[str, Any]]) -> str:
    """Return the id of the single test location, creating it when absent."""
    found = await database["locations"].find_one({"slug": LOCATION_SLUG})
    if found:
        return str(found["_id"])
    now = datetime.now(UTC).replace(microsecond=0)
    result = await database["locations"].insert_one(
        {
            "name": "Visual test location",
            "slug": LOCATION_SLUG,
            "type": "museum",
            "city": "Test",
            "country": "Test",
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
    )
    return str(result.inserted_id)


async def object_ids(
    database: AsyncDatabase[dict[str, Any]], location_id: str
) -> dict[str, str]:
    rows = await database["objects"].find({"location_id": location_id}).to_list()
    return {r["external_id"]: str(r["_id"]) for r in rows if r.get("external_id")}


async def build_references(
    root: Path,
    database: AsyncDatabase[dict[str, Any]],
    encoder: ImageEncoder,
    lock: asyncio.Lock,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Embed references/ into the test database with the application's own pipeline.

    Mirrors `preparation.prepare`: prepare_image, `encoder.describe(regions=True)`,
    the same reference record and `VisualRepository.publish`. Only the image source
    differs (local files instead of allowlisted HTTPS downloads).
    """
    repository = VisualRepository(database)
    location_id = await ensure_location(database)
    folders = [(name, kind, [image]) for name, kind, image in references(root)]
    total = len(folders)
    report: dict[str, Any] = {
        "prepared": 0,
        "skipped": 0,
        "removed": 0,
        "images": total,
    }
    done = 0
    now = datetime.now(UTC)
    existing = await object_ids(database, location_id)
    for name, kind, files in folders:
        source = await database["sources"].find_one(
            {"location_id": location_id, "external_id": name}
        )
        if source is None:
            source_id = (
                await database["sources"].insert_one(
                    {
                        "location_id": location_id,
                        "external_id": name,
                        "title": name,
                        "type": "manual",
                        "url": None,
                        "raw_record": {"folder": name},
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            ).inserted_id
        else:
            source_id = source["_id"]
        if name in existing:
            await database["objects"].update_one(
                {"_id": ObjectId(existing[name])}, {"$set": {"type": kind}}
            )
            object_id = existing[name]
        else:
            object_id = str(
                (
                    await database["objects"].insert_one(
                        {
                            "location_id": location_id,
                            "external_id": name,
                            "name": name,
                            "type": kind,
                            "aliases": [],
                            "facts": [],
                            "source_ids": [str(source_id)],
                            "metadata": {},
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                ).inserted_id
            )
        obj = await database["objects"].find_one({"_id": ObjectId(object_id)})
        assert obj is not None
        sources = await repository.sources(obj)
        previous = await repository.current(location_id, object_id)
        views = []
        for image in files:
            raw = await asyncio.to_thread(image.read_bytes)
            image_hash = hashlib.sha256(raw).hexdigest()
            old = next(
                (
                    v
                    for v in (previous or {}).get("views", [])
                    if previous
                    and v["image_hash"] == image_hash
                    and previous["model"] == encoder.model
                    and previous["preprocessing"] == encoder.preprocessing
                ),
                None,
            )
            if old is not None:
                views.append(old)
            else:
                clean = await asyncio.to_thread(
                    prepare_image, raw, EXTENSIONS[image.suffix.lower()]
                )
                ref = {"file": image.name, "object": name}
                async with lock:
                    derived = await asyncio.to_thread(
                        encoder.describe, clean, regions=True
                    )
                views.append(
                    {
                        "reference": ref,
                        "metadata_hash": digest(ref),
                        "image_hash": image_hash,
                        "normalized_image_hash": hashlib.sha256(clean).hexdigest(),
                        "derived": derived,
                    }
                )
            done += 1
            if progress:
                progress(done, total)
        current_hash = evidence_hash(obj, sources)
        if (
            previous is not None
            and digest(views) == digest(previous["views"])
            and previous["evidence_hash"] == current_hash
        ):
            report["skipped"] += 1
            continue
        await repository.publish(
            {
                "location_id": location_id,
                "object_id": object_id,
                "model": encoder.model,
                "preprocessing": encoder.preprocessing,
                "evidence_hash": current_hash,
                "set_id": uuid4().hex,
                "views": views,
                "vector_count": sum(len(v["derived"]["regions"]) for v in views),
                "warnings": [],
                "fetched_at": now,
            },
            previous,
        )
        report["prepared"] += 1
    keep = {name for name, _, _ in folders}
    for name, object_id in existing.items():
        if name in keep:
            continue
        previous = await repository.current(location_id, object_id)
        if previous:
            await repository.vectors.delete_many(
                {"location_id": location_id, "set_id": previous["set_id"]}
            )
            await repository.references.delete_one({"_id": previous["_id"]})
        await database["objects"].delete_one({"_id": ObjectId(object_id)})
        report["removed"] += 1
    await repository.ensure_indexes()
    report["index"] = await repository.index_status(create=True)
    return report


async def run_dataset(
    root: Path,
    photos: list[EvalPhoto],
    database: AsyncDatabase[dict[str, Any]],
    encoder: ImageEncoder,
    lock: asyncio.Lock,
    progress: Callable[[int], None] | None = None,
    threshold: float = 0.45,
) -> list[dict[str, Any]]:
    location_id = await ensure_location(database)
    ids = await object_ids(database, location_id)
    if any(p.object is not None and p.object not in ids for p in photos):
        raise EvaluationError("REFERENCES_NOT_BUILT")
    service = VisualService(VisualRepository(database), encoder, lock)
    results = []
    for done, photo in enumerate(photos, 1):
        path = photo_path(root, photo)
        response = await service.recognize(
            location_id,
            await asyncio.to_thread(path.read_bytes),
            mimetypes.guess_type(path.name)[0],
        )
        results.append(
            judge(
                photo,
                ids[photo.object] if photo.object else None,
                response.status,
                [c.model_dump() for c in response.candidates],
                threshold,
            )
        )
        if progress:
            progress(done)
    return results
