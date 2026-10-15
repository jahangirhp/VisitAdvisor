"""Replay frozen artwork/room cases through uploads and real browser JPEG crops.

Read-only Atlas operations; no model-provider calls or saved query frames. Static
suggestions are policy simulations, not measured three-frame tracking or phone use.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import math
import mimetypes
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.main import create_app
from app.modules.live_camera.references import AtlasCameraReferences
from app.modules.live_camera.server import create_camera_app
from app.modules.visual.benchmark import Photo, inspect_photos, validate_manifest
from app.modules.visual.features import DinoEncoder, digest
from app.modules.visual.repository import VisualRepository

CODE_FILES = (
    "app/modules/visual/features.py",
    "app/modules/visual/repository.py",
    "app/modules/visual/service.py",
    "app/modules/live_camera/inference.py",
    "app/modules/live_camera/references.py",
    "app/modules/live_camera/static/capture.mjs",
    "scripts/camera_frames.mjs",
)


class RoomPhoto(Photo):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_page: str = Field(min_length=1)
    license: str = Field(min_length=1)
    room_only: bool = False

    @model_validator(mode="after")
    def room_is_negative(self) -> "RoomPhoto":
        if self.room_only and self.expected_object_id:
            raise ValueError("Room-only negatives cannot have an artwork identity")
        return self


class ReplayManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["regression", "acceptance"]
    independence_reviewed: bool = False
    camera_object_ids: list[str] = Field(min_length=2, max_length=12)
    photos: list[RoomPhoto] = Field(min_length=1, max_length=300)


def proportion(correct: int, total: int) -> dict[str, Any]:
    if not total:
        return {"correct": correct, "total": 0, "rate": None, "wilson95": None}
    z = 1.959963984540054
    p = correct / total
    scale = 1 + z * z / total
    center = (p + z * z / (2 * total)) / scale
    half = z / scale * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return {
        "correct": correct,
        "total": total,
        "rate": p,
        "wilson95": [max(0.0, center - half), min(1.0, center + half)],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in rows if r["expected_object_id"]]
    negatives = [r for r in rows if r["room_only"]]
    suggestions = [r for r in rows if r["static_suggestion"]]
    correct = sum(
        r["static_suggestion"] == r["expected_object_id"] for r in suggestions
    )
    result = {
        "top1": proportion(sum(r["top1_correct"] for r in positives), len(positives)),
        "top3": proportion(sum(r["top3_correct"] for r in positives), len(positives)),
        "suggestion_precision": proportion(correct, len(suggestions)),
        "correct_suggestion_coverage": proportion(correct, len(positives)),
        "wrong_static_suggestions": len(suggestions) - correct,
        "room_only_false_suggestions": sum(
            bool(r["static_suggestion"]) for r in negatives
        ),
        "room_only_count": len(negatives),
        "service_failures": sum(r["http_status"] != 200 for r in rows),
        "abstentions": sum(
            r["http_status"] == 200 and not r["static_suggestion"] for r in rows
        ),
        "confusion": dict(
            Counter(
                f"{r['expected_object_id'] or 'room-only'} -> "
                f"{r['static_suggestion'] or abstention(r)}"
                for r in rows
            )
        ),
    }
    result["gates"] = {
        "top1_90_percent": bool(positives) and result["top1"]["rate"] >= 0.9,
        "top3_95_percent": bool(positives) and result["top3"]["rate"] >= 0.95,
        "zero_wrong_static_suggestions": result["wrong_static_suggestions"] == 0,
        "zero_room_only_suggestions": len(negatives) >= 20
        and result["room_only_false_suggestions"] == 0,
        "correct_suggestion_coverage_50_percent": bool(positives)
        and result["correct_suggestion_coverage"]["rate"] >= 0.5,
        "all_requests_valid": bool(rows) and result["service_failures"] == 0,
    }
    return result


def dataset_gates(manifest: ReplayManifest) -> dict[str, bool]:
    photos = manifest.photos
    positives = [p for p in photos if p.expected_object_id]
    counts = Counter(p.expected_object_id for p in positives)
    groups = Counter(
        oid
        for oid in counts
        for _ in {p.capture_group for p in positives if p.expected_object_id == oid}
    )
    return {
        "new_reviewed_holdout": manifest.purpose == "acceptance"
        and manifest.independence_reviewed
        and all(p.independent for p in photos),
        "thirty_artwork_photos": len(positives) >= 30,
        "five_artworks_six_captures_each": len(counts) >= 5
        and all(groups[oid] >= 6 for oid in counts),
        "twenty_room_only_negatives": sum(p.room_only for p in photos) >= 20,
        "evaluation_only": all(p.split == "evaluation" for p in photos),
    }


def abstention(row: dict[str, Any]) -> str:
    return "abstain" if row["http_status"] == 200 else "error"


def browser_frames(
    manifest: ReplayManifest, root: Path, browser: str, frame_size: int = 224
) -> list[bytes]:
    request = {
        "browser": browser,
        "frame_size": frame_size,
        "paths": [str((root / p.path).resolve()) for p in manifest.photos],
    }
    process = subprocess.run(
        ["node", "scripts/camera_frames.mjs"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if process.returncode:
        raise ValueError("CAMERA_PREPROCESSING_FAILED")
    frames = [json.loads(line) for line in process.stdout.splitlines()]
    if [r["path"] for r in frames] != request["paths"]:
        raise ValueError("CAMERA_FRAME_ORDER_CHANGED")
    return [base64.b64decode(r["jpeg"], validate=True) for r in frames]


def historical_image_hashes() -> set[str]:
    """Reject relabelled regression/reference bytes as a fresh acceptance holdout."""
    hashes: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                visit(
                    item,
                    name
                    if key not in ("photo_hashes", "normalized_photo_hashes")
                    else key,
                )
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
        elif isinstance(value, str) and key in {
            "sha256",
            "photo_sha256",
            "image_hash",
            "normalized_hash",
            "photo_hashes",
            "normalized_photo_hashes",
        }:
            hashes.add(value)

    for path in [
        *Path("docs").glob("*.json"),
        *Path("tests/data/visual").rglob("*.json"),
    ]:
        visit(json.loads(path.read_text()))
    return hashes


async def run(
    path: Path, browser: str, verify: bool, verify_camera: bool = False
) -> dict[str, Any]:
    raw_manifest = path.read_bytes()
    code_hash = digest({p: Path(p).read_text() for p in CODE_FILES})
    manifest = ReplayManifest.model_validate_json(raw_manifest)
    photos = manifest.photos
    validate_manifest(photos)
    if len({p.location_id for p in photos}) != 1:
        raise ValueError("Replay needs exactly one selected location")
    settings = Settings().model_copy(
        update={
            "openai_api_key": None,
            "visual_enabled": True,
            "visual_geometry_object_ids": manifest.camera_object_ids if verify else [],
            "live_camera_object_ids": manifest.camera_object_ids,
            "live_camera_location_id": photos[0].location_id,
            "live_camera_geometry_rerank": verify_camera,
        }
    )
    # Validate configured IDs even though model_copy deliberately bypasses settings
    # construction. This also checks the selected location's covered reference set.
    raw_hashes, normalized_hashes = inspect_photos(photos, path.parent)
    if any(raw_hashes[p.id] != p.sha256 for p in photos):
        raise ValueError("PHOTO_HASH_CHANGED")
    if any(
        p.expected_object_id and p.expected_object_id not in manifest.camera_object_ids
        for p in photos
    ):
        raise ValueError(
            "Expected artwork is missing from the competing camera catalog"
        )
    frames = await asyncio.to_thread(browser_frames, manifest, path.parent, browser)
    db = MongoDB(settings)
    try:
        encoder = await asyncio.to_thread(DinoEncoder)
        repository = VisualRepository(db.database)
        references = AtlasCameraReferences(
            db.database, settings, settings.live_camera_bundle
        )
        engine = await references.get()
        frozen_version = engine.reference_version
        active, _ = await repository.active_snapshot(
            photos[0].location_id, encoder.model, encoder.preprocessing
        )
        frozen_sets = {r["object_id"]: r["set_id"] for r in active}
        application = create_app()
        application.state.settings = settings
        application.state.mongodb = db
        application.state.visual_encoder = encoder
        application.state.visual_lock = asyncio.Lock()
        camera = create_camera_app()
        camera.state.references = references
        application.mount("/replay-camera", camera)
        rows = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            for photo, frame in zip(photos, frames, strict=True):
                original = (path.parent / photo.path).read_bytes()
                if hashlib.sha256(original).hexdigest() != photo.sha256:
                    raise ValueError("PHOTO_CHANGED_DURING_REPLAY")
                for method in ("upload", "camera"):
                    start = time.perf_counter()
                    if method == "upload":
                        response = await client.post(
                            "/api/v1/recognition/image",
                            data={"location_id": photo.location_id, "mode": "visual"},
                            files={
                                "image": (
                                    "scan.jpg",
                                    original,
                                    mimetypes.guess_type(photo.path)[0] or "image/jpeg",
                                )
                            },
                        )
                    else:
                        response = await client.post(
                            "/replay-camera/recognize",
                            params={
                                "location_id": photo.location_id,
                                "reference_version": frozen_version,
                            },
                            content=frame,
                            headers={"Content-Type": "image/jpeg"},
                        )
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    candidates = [
                        {"id": c.get("id", c.get("object_id")), "score": c["score"]}
                        for c in body.get("candidates", [])
                    ]
                    policy = engine.catalog["policy"]
                    first = candidates[0] if candidates else None
                    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
                    suggestion = (
                        first["id"]
                        if response.status_code == 200
                        and first
                        and first["score"] >= policy["candidate_floor"]
                        and first["score"] - second >= policy["candidate_margin"]
                        else None
                    )
                    rows.append(
                        {
                            "photo_id": photo.id,
                            "path": method,
                            "http_status": response.status_code,
                            "expected_object_id": photo.expected_object_id,
                            "room_only": photo.room_only,
                            "capture_group": photo.capture_group,
                            "candidates": candidates,
                            "top1_correct": bool(
                                photo.expected_object_id
                                and first
                                and first["id"] == photo.expected_object_id
                            ),
                            "top3_correct": bool(
                                photo.expected_object_id
                                and photo.expected_object_id
                                in [c["id"] for c in candidates]
                            ),
                            "static_suggestion": suggestion,
                            "method": body.get("method", body.get("version")),
                            "reference_version": body.get("reference_version"),
                            "seconds": time.perf_counter() - start,
                            "inference_ms": body.get("inferenceMs"),
                            "request_sha256": hashlib.sha256(
                                frame if method == "camera" else original
                            ).hexdigest(),
                        }
                    )
                    print(
                        f"{photo.id} {method}: HTTP {response.status_code}", flush=True
                    )
                # Respect the camera server's existing four-starts/second budget.
                await asyncio.sleep(0.26)
        after, _ = await repository.active_snapshot(
            photos[0].location_id, encoder.model, encoder.preprocessing
        )
        final_version, _, _ = await references.snapshot()
        gates = dataset_gates(manifest)
        historical = historical_image_hashes()
        gates["no_historical_query_or_reference_bytes"] = not historical.intersection(
            set(raw_hashes.values()) | set(normalized_hashes.values())
        )
        gates["references_unchanged"] = (
            frozen_sets == {r["object_id"]: r["set_id"] for r in after}
            and final_version == frozen_version
        )
        gates["manifest_unchanged"] = path.read_bytes() == raw_manifest
        gates["code_unchanged"] = code_hash == digest(
            {p: Path(p).read_text() for p in CODE_FILES}
        )
        final_raw, final_normalized = inspect_photos(photos, path.parent)
        gates["photos_unchanged"] = (
            final_raw == raw_hashes and final_normalized == normalized_hashes
        )
        paths = {
            method: summarize([r for r in rows if r["path"] == method])
            for method in ("upload", "camera")
        }
        return {
            "created_at": datetime.now(UTC).isoformat(),
            "purpose": manifest.purpose,
            "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
            "photo_hashes": raw_hashes,
            "normalized_photo_hashes": normalized_hashes,
            "browser": browser,
            "model": encoder.model,
            "preprocessing": encoder.preprocessing,
            "model_sha256": engine.catalog["model"]["sha256"],
            "reference_sets": frozen_sets,
            "reference_version": frozen_version,
            "camera_policy": engine.catalog["policy"],
            "upload_policy": "bounded-geometry-v1"
            if verify
            else "regional-embeddings-v1",
            "code_sha256": code_hash,
            "dataset_gates": gates,
            "paths": paths,
            "by_object": {
                oid: {
                    method: summarize(
                        [
                            r
                            for r in rows
                            if r["expected_object_id"] == oid and r["path"] == method
                        ]
                    )
                    for method in paths
                }
                for oid in sorted(
                    {p.expected_object_id for p in photos if p.expected_object_id}
                )
            },
            "acceptance_passed": all(gates.values())
            and all(all(p["gates"].values()) for p in paths.values()),
            "suggestions_are_static_policy_simulations": True,
            "tracking_measured": False,
            "physical_phone_measured": False,
            "automatic_identification": False,
            "latency_scope": "In-process HTTP, browser-encoded fixture JPEG, "
            "local CPU and live Atlas; excludes physical camera/mobile network",
            "rows": rows,
        }
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="webkit")
    parser.add_argument("--verify-upload", action="store_true")
    parser.add_argument("--verify-camera", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve frozen evidence")
    try:
        report = asyncio.run(
            run(args.manifest, args.browser, args.verify_upload, args.verify_camera)
        )
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    except Exception:
        print(
            "REPLAY_FAILED: check frozen inputs, browser/model installation "
            "and Atlas readiness."
        )
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "dataset_gates": report["dataset_gates"],
                "paths": report["paths"],
                "acceptance_passed": report["acceptance_passed"],
            },
            indent=2,
        )
    )
    raise SystemExit(0 if report["acceptance_passed"] else 1)


if __name__ == "__main__":
    main()
