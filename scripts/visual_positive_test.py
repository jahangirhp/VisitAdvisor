"""Evaluate a labelled positive-photo folder through the visual upload API.

Uses cached DINO weights and live Atlas reads. No OCR/provider calls or DB writes.
Scores are similarities, not confidence probabilities. Labels must be frozen first.
"""

import argparse
import asyncio
import hashlib
import json
import mimetypes
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.domain import validate_id
from app.db.mongodb import MongoDB
from app.main import create_app
from app.modules.recognition.images import MAX_IMAGE_BYTES, prepare_image
from app.modules.visual.repository import VisualRepository


class PositivePhoto(BaseModel):
    file: str
    object_id: str
    location_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    capture_group: str = Field(min_length=1)
    source_page: str = Field(min_length=1)
    license: str = Field(min_length=1)
    category: str = Field(min_length=1)
    object_type: str = "unspecified"


def load_inputs(folder: Path) -> tuple[list[PositivePhoto], str]:
    folder = folder.resolve()
    manifest = folder / "manifest.json"
    raw = manifest.read_bytes()
    photos = [PositivePhoto.model_validate(row) for row in json.loads(raw)["images"]]
    if not 1 <= len(photos) <= 3000 or len({p.file for p in photos}) != len(photos):
        raise ValueError("Manifest must contain 1–3000 distinct positive photos")
    files = {
        str(p.relative_to(folder))
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    }
    if files != {p.file for p in photos}:
        raise ValueError(
            "Every photo in the folder must have exactly one manifest label"
        )
    if len({p.source_page for p in photos}) != len(photos):
        raise ValueError("Use one test image per separate source photo")
    if len({p.sha256 for p in photos}) != len(photos):
        raise ValueError("Duplicate image bytes in positive folder")
    for photo in photos:
        validate_id(photo.object_id)
        validate_id(photo.location_id)
        path = (folder / photo.file).resolve()
        if not path.is_relative_to(folder):
            raise ValueError("Photo path escapes the input folder")
        with path.open("rb") as handle:
            data = handle.read(MAX_IMAGE_BYTES + 1)
        if hashlib.sha256(data).hexdigest() != photo.sha256:
            raise ValueError("Photo checksum does not match its frozen label")
        prepare_image(data, mimetypes.guess_type(photo.file)[0])
    return photos, hashlib.sha256(raw).hexdigest()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    top1 = sum(row["top1_correct"] for row in rows)
    top3 = sum(row["top3_correct"] for row in rows)
    failures = sum(row["http_status"] != 200 for row in rows)
    return {
        "photos": total,
        "top1_correct": top1,
        "top1_accuracy": top1 / total if total else None,
        "top3_correct": top3,
        "top3_recall": top3 / total if total else None,
        "service_failures": failures,
        "service_failure_rate": failures / total if total else None,
        "no_candidates": sum(
            row["http_status"] == 200 and not row["candidates"] for row in rows
        ),
        "statuses": dict(Counter(row["status"] for row in rows)),
    }


async def run(folder: Path) -> dict[str, Any]:
    photos, manifest_hash = load_inputs(folder)
    # Override settings for this in-process API only; no provider key is used.
    settings = Settings().model_copy(
        update={"openai_api_key": None, "visual_enabled": True}
    )
    mongodb = MongoDB(settings)
    rows: list[dict[str, Any]] = []
    try:
        from app.modules.visual.features import DinoEncoder

        encoder = DinoEncoder()
        repository = VisualRepository(mongodb.database)

        async def reference_sets() -> dict[str, dict[str, str]]:
            result = {}
            for location_id in sorted({p.location_id for p in photos}):
                records, _ = await repository.active_snapshot(
                    location_id,
                    encoder.model,
                    encoder.preprocessing,
                    include_features=False,
                )
                result[location_id] = {r["object_id"]: r["set_id"] for r in records}
            return result

        before = await reference_sets()
        if any(p.object_id not in before[p.location_id] for p in photos):
            raise ValueError("A positive object lacks current compatible references")
        application = create_app()
        application.state.settings = settings
        application.state.mongodb = mongodb
        application.state.visual_encoder = encoder
        application.state.visual_lock = asyncio.Lock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://testserver",
            timeout=60,
        ) as client:
            for photo in photos:
                start = time.perf_counter()
                body: dict[str, Any] = {}
                http_status = None
                error_code = None
                try:
                    response = await client.post(
                        "/api/v1/recognition/image",
                        data={"location_id": photo.location_id, "mode": "visual"},
                        files={
                            "image": (
                                photo.file,
                                (folder / photo.file).read_bytes(),
                                mimetypes.guess_type(photo.file)[0],
                            )
                        },
                    )
                    http_status = response.status_code
                    if http_status == 200:
                        body = response.json()
                    else:
                        error_code = "VISUAL_HTTP_ERROR"
                except httpx.HTTPError:
                    error_code = "VISUAL_REQUEST_FAILED"
                candidates = body.get("candidates", [])[:3]
                ids = [c["object_id"] for c in candidates]
                row = {
                    "file": photo.file,
                    "expected_object_id": photo.object_id,
                    "location_id": photo.location_id,
                    "capture_group": photo.capture_group,
                    "category": photo.category,
                    "object_type": photo.object_type,
                    "photo_sha256": photo.sha256,
                    "http_status": http_status,
                    "status": body.get("status", "error"),
                    "error_code": error_code,
                    "candidates": [
                        {k: c[k] for k in ("object_id", "name", "score")}
                        for c in candidates
                    ],
                    "top1_correct": bool(ids and ids[0] == photo.object_id),
                    "top3_correct": photo.object_id in ids,
                    "top_score_margin": candidates[0]["score"] - candidates[1]["score"]
                    if len(candidates) >= 2
                    else None,
                    "warnings": body.get("warnings", []),
                    "covered_objects": body.get("covered_objects"),
                    "total_objects": body.get("total_objects"),
                    "seconds": time.perf_counter() - start,
                }
                rows.append(row)
                print(json.dumps(row), flush=True)
        after = await reference_sets()
        return {
            "reference_sets": before,
            "reference_sets_unchanged": before == after,
            "created_at": datetime.now(UTC).isoformat(),
            "folder": str(folder),
            "manifest_sha256": manifest_hash,
            "model": encoder.model,
            "preprocessing": encoder.preprocessing,
            "mode": "visual",
            "scores_are_probabilities": False,
            "measurement": "In-process visual API uploads and live Atlas reads; "
            "full location catalog; no browser crop/tracking or mobile network",
            "dataset": "Positive-only diagnostic; no negative rejection or "
            "confidence calibration measured. Derivatives share their source capture.",
            "summary": summarize(rows),
            "by_object": {
                object_id: summarize(
                    [r for r in rows if r["expected_object_id"] == object_id]
                )
                for object_id in sorted({p.object_id for p in photos})
            },
            "by_object_type": {
                kind: summarize([r for r in rows if r["object_type"] == kind])
                for kind in sorted({p.object_type for p in photos})
            },
            "by_category": {
                category: summarize([r for r in rows if r["category"] == category])
                for category in sorted({p.category for p in photos})
            },
            "rows": rows,
        }
    finally:
        await mongodb.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder", type=Path, help="Folder containing photos and manifest.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--strict", action="store_true", help="Fail for any wrong top-1 result"
    )
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new report path")
    try:
        report = asyncio.run(run(args.folder))
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    except Exception:
        print("POSITIVE_TEST_FAILED: check labels, cached model and Atlas readiness.")
        raise SystemExit(1) from None
    print(json.dumps(report["summary"], indent=2))
    if (
        not report["reference_sets_unchanged"]
        or report["summary"]["service_failures"]
        or (
            args.strict
            and report["summary"]["top1_correct"] != report["summary"]["photos"]
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
