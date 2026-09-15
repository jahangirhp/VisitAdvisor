"""Exercise real FastAPI uploads and Atlas with a reviewed public-photo manifest.

This is an in-process ASGI measurement, not phone/network latency. It never enables
automatic matching or calls an OCR/model provider. Normal app startup initializes
its existing indexes; object, source and reference records are not changed.
"""

import argparse
import hashlib
import json
import math
import mimetypes
import os
import platform
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.visual.__main__ import read_json
from app.modules.visual.benchmark import Photo, load_photo, validate_manifest
from app.modules.visual.features import digest


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in rows if r["expected_object_id"]]
    negatives = [r for r in rows if not r["expected_object_id"]]
    successful = [r for r in rows if r["http_status"] == 200]
    return {
        "requests": len(rows),
        "http_200": len(successful),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "positive_photos": len(positives),
        "positive_top3_correct": sum(r["top3_correct"] for r in positives),
        "negative_photos": len(negatives),
        "negative_with_candidates": sum(bool(r["candidates"]) for r in negatives),
        "negative_abstentions": sum(
            r["http_status"] == 200
            and r["status"] in ("no_match", "unreadable")
            and not r["candidates"]
            for r in negatives
        ),
        "automatic_matches": sum(r["status"] == "matched" for r in rows),
        "p95_all_requests_seconds": sorted(r["seconds"] for r in rows)[
            math.ceil(0.95 * len(rows)) - 1
        ],
    }


def run(manifest: Path) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    photos = [Photo.model_validate(row) for row in read_json(str(manifest))]
    validate_manifest(photos)
    root = manifest.resolve().parent
    for photo in photos:
        load_photo(root, photo.path)
    # Overrides affect this process only; visual tests require no provider key.
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["VISUAL_ENABLED"] = "true"
    rows = []
    with TestClient(create_app()) as client:
        encoder = client.app.state.visual_encoder  # type: ignore[union-attr]
        model, preprocessing = encoder.model, encoder.preprocessing
        for photo in photos:
            data = (root / photo.path).read_bytes()
            start = time.perf_counter()
            response = client.post(
                "/api/v1/recognition/image",
                data={"location_id": photo.location_id, "mode": "visual"},
                files={
                    "image": (
                        "scan.jpg",
                        data,
                        mimetypes.guess_type(photo.path)[0],
                    )
                },
            )
            seconds = time.perf_counter() - start
            body = response.json()
            candidates = [c["object_id"] for c in body.get("candidates", [])]
            rows.append(
                {
                    "id": photo.id,
                    "split": photo.split,
                    "category": photo.category,
                    "object_type": photo.object_type,
                    "expected_object_id": photo.expected_object_id,
                    "photo_hash": hashlib.sha256(data).hexdigest(),
                    "http_status": response.status_code,
                    "status": body.get("status", "error"),
                    "error_code": body.get("error", {}).get("code"),
                    "candidates": candidates,
                    "scores": [c["score"] for c in body.get("candidates", [])],
                    "top3_correct": photo.expected_object_id in candidates,
                    "ocr": body.get("ocr"),
                    "warnings": body.get("warnings", []),
                    "seconds": seconds,
                }
            )
            print(
                json.dumps(
                    {
                        "id": photo.id,
                        "http_status": response.status_code,
                        "seconds": round(seconds, 3),
                    }
                ),
                flush=True,
            )
    return {
        "started_at": started_at,
        "model": model,
        "preprocessing": preprocessing,
        "manifest_hash": digest([p.model_dump() for p in photos]),
        "measurement": "Sequential in-process ASGI multipart uploads and real Atlas; "
        "excludes startup, camera capture, mobile network and public HTTP transport",
        "hardware": {"machine": platform.machine(), "processor": platform.processor()},
        "summary": summarize(rows),
        "by_split": {
            split: summarize([r for r in rows if r["split"] == split])
            for split in sorted({r["split"] for r in rows})
        },
        "rows": rows,
        "automatic_precision_validated": False,
        "phase5_complete": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run(args.manifest)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    except Exception:
        print(json.dumps({"error": {"code": "API_SMOKE_RUN_FAILED"}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
