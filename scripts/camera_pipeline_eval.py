"""Freeze, compare and calibrate the bounded real-time camera pipeline.

Runs actual browser crop/JPEG encoding and the camera HTTP handler. Atlas reads
only; no provider calls or saved frames. Phone latency/tracking remain separate.
"""

import argparse
import asyncio
import hashlib
import json
import math
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.live_camera.pipeline import PIPELINE_VERSION, profile, view_coverage
from app.modules.live_camera.policy import (
    CameraCalibration,
    ProfileThresholds,
    pipeline_hash,
)
from app.modules.live_camera.references import AtlasCameraReferences
from app.modules.live_camera.server import create_camera_app
from app.modules.visual.benchmark import inspect_photos, validate_manifest
from app.modules.visual.calibration import fit_thresholds
from app.modules.visual.features import digest
from scripts.artwork_room_replay import (
    ReplayManifest,
    browser_frames,
    historical_image_hashes,
    proportion,
)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in rows if r["expected_object_id"]]
    negatives = [r for r in rows if not r["expected_object_id"]]
    suggested = [r for r in rows if r["suggestion"]]
    correct = sum(r["suggestion"] == r["expected_object_id"] for r in suggested)
    times = sorted(r["seconds"] for r in rows)
    return {
        "attempts": len(rows),
        "service_failures": sum(r["http_status"] != 200 for r in rows),
        "top1": proportion(sum(r["top1_correct"] for r in positives), len(positives)),
        "top3": proportion(sum(r["top3_correct"] for r in positives), len(positives)),
        "suggestion_precision": proportion(correct, len(suggested)),
        "correct_suggestion_coverage": proportion(correct, len(positives)),
        "negative_false_suggestions": sum(bool(r["suggestion"]) for r in negatives),
        "negative_count": len(negatives),
        "p95_request_seconds": times[math.ceil(len(times) * 0.95) - 1]
        if times
        else None,
        "budget_exceeded": sum(
            "INFERENCE_BUDGET_EXCEEDED" in r["warnings"] for r in rows
        ),
        "max_model_calls": max(
            (r["timings"].get("model_calls", 1) for r in rows), default=0
        ),
        "abstentions": sum(
            r["http_status"] == 200 and not r["suggestion"] for r in rows
        ),
    }


def fit_profiles(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, ProfileThresholds], dict[str, Any]]:
    thresholds = {}
    reports = {}
    for kind in ("planar", "multiview"):
        positives = [
            r for r in rows if r["expected_object_id"] and r["expected_profile"] == kind
        ]
        negatives = [r for r in rows if not r["expected_object_id"]]
        identities = {r["expected_object_id"] for r in positives}
        complete = (
            len(positives) >= 10 and len(negatives) >= 10 and len(identities) >= 2
        )
        usable = all(r["http_status"] == 200 for r in rows)
        report: dict[str, Any] = {
            "positives": len(positives),
            "negatives": len(negatives),
            "identities": len(identities),
        }
        if not complete or not usable:
            thresholds[kind] = ProfileThresholds(floor=1, margin=1, enabled=False)
            report["status"] = (
                "insufficient_samples" if not complete else "service_failures"
            )
        else:
            observations = []
            for row in rows:
                candidates = row["candidates"]
                first = candidates[0] if candidates else None
                # Keep wrong-profile predictions and all negatives in the fitting
                # evidence. Never rerank candidates using the ground-truth label.
                scores = (
                    [{"object_id": c["id"], "score": c["score"]} for c in candidates]
                    if first
                    and first.get("profile") == kind
                    and first.get("coverage_ready", False)
                    and not row["budget_exceeded"]
                    else []
                )
                observations.append(
                    {
                        "expected_object_id": row["expected_object_id"],
                        "independent": row["independent"],
                        "scores": scores,
                    }
                )
            fitted, details = fit_thresholds(observations, 0.55)
            thresholds[kind] = ProfileThresholds(
                floor=max(0.55, fitted.automatic) if fitted.automatic_enabled else 1,
                margin=max(0.02, fitted.margin) if fitted.automatic_enabled else 1,
                enabled=fitted.automatic_enabled,
            )
            report.update(details)
        reports[kind] = report
    return thresholds, reports


async def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_bytes = args.manifest.read_bytes()
    manifest = ReplayManifest.model_validate_json(manifest_bytes)
    validate_manifest(manifest.photos)
    if len({p.location_id for p in manifest.photos}) != 1:
        raise ValueError("One selected location is required")
    if len(set(manifest.camera_object_ids)) != len(manifest.camera_object_ids):
        raise ValueError("Duplicate camera identities")
    if any(
        p.expected_object_id and p.expected_object_id not in manifest.camera_object_ids
        for p in manifest.photos
    ):
        raise ValueError("A labelled positive is absent from the competing catalog")
    raw, normalized = inspect_photos(manifest.photos, args.manifest.parent)
    if any(raw[p.id] != p.sha256 for p in manifest.photos):
        raise ValueError("Changed photo")
    manifest_hash = digest(manifest.model_dump())
    artifact = (
        CameraCalibration.model_validate_json(args.calibration.read_bytes())
        if args.calibration
        else None
    )
    if artifact:
        artifact.verify()
        if (
            artifact.manifest_hash != manifest_hash
            or artifact.normalized_photo_hashes != normalized
        ):
            raise ValueError("Calibration inputs changed")
    split = (
        "calibration"
        if args.phase == "calibrate"
        else "evaluation"
        if args.phase == "validate"
        else args.split
    )
    selected = (
        [p for p in manifest.photos if p.split == split]
        if split != "all"
        else manifest.photos
    )
    if not selected:
        raise ValueError("Empty selected split")
    if args.phase == "calibrate" and not all(p.independent for p in selected):
        raise ValueError("Independent calibration captures are required")
    frame_manifest = manifest.model_copy(update={"photos": selected})
    frames = await asyncio.to_thread(
        browser_frames,
        frame_manifest,
        args.manifest.parent,
        args.browser,
        224 if args.mode == "legacy" else 512,
    )
    settings = Settings().model_copy(
        update={
            "openai_api_key": None,
            "live_camera_location_id": selected[0].location_id,
            "live_camera_object_ids": manifest.camera_object_ids,
            "live_camera_geometry_rerank": False,
            "live_camera_adaptive": args.mode != "legacy",
            "live_camera_object_regions": args.mode == "regions",
            "live_camera_calibration": args.calibration,
            "live_camera_inference_budget_ms": args.budget_ms,
        }
    )
    frozen_code = pipeline_hash()
    db = MongoDB(settings)
    try:
        startup = time.perf_counter()
        references = AtlasCameraReferences(
            db.database, settings, settings.live_camera_bundle
        )
        engine = await references.get()
        startup_seconds = time.perf_counter() - startup
        version = engine.reference_version
        objects = {o["id"]: o for o in engine.catalog["objects"]}
        reference_readiness = {oid: view_coverage(o) for oid, o in objects.items()}
        app = create_camera_app()
        app.state.references = references
        rows = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            for photo, frame in zip(selected, frames, strict=True):
                if (
                    hashlib.sha256(
                        (args.manifest.parent / photo.path).read_bytes()
                    ).hexdigest()
                    != photo.sha256
                ):
                    raise ValueError("Photo changed during inference")
                start = time.perf_counter()
                try:
                    response = await client.post(
                        "/recognize",
                        params={
                            "location_id": photo.location_id,
                            "reference_version": version,
                        },
                        headers={"Content-Type": "image/jpeg"},
                        content=frame,
                    )
                    status = response.status_code
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                except httpx.HTTPError:
                    status, body = 0, {}
                seconds = time.perf_counter() - start
                candidates = body.get("candidates", [])
                first = candidates[0] if candidates else None
                second = candidates[1]["score"] if len(candidates) > 1 else 0.0
                policy = engine.catalog["policy"]
                threshold = (
                    policy.get("profiles", {}).get(first.get("profile"), {})
                    if first
                    else {}
                )
                allowed = body.get("suggestion_allowed", True)
                suggestion = (
                    first["id"]
                    if status == 200
                    and allowed
                    and first
                    and threshold.get("enabled", True)
                    and first["score"]
                    >= threshold.get("floor", policy["candidate_floor"])
                    and first["score"] - second
                    >= threshold.get("margin", policy["candidate_margin"])
                    else None
                )
                row = {
                    "photo_id": photo.id,
                    "expected_object_id": photo.expected_object_id,
                    "expected_profile": profile(
                        objects[photo.expected_object_id].get("type", "other")
                    )
                    if photo.expected_object_id
                    else None,
                    "object_type": photo.object_type,
                    "category": photo.category,
                    "split": photo.split,
                    "capture_group": photo.capture_group,
                    "independent": photo.independent,
                    "source_page": photo.source_page,
                    "license": photo.license,
                    "http_status": status,
                    "seconds": seconds,
                    "candidates": candidates,
                    "suggestion": suggestion,
                    "top1_correct": bool(
                        first
                        and photo.expected_object_id
                        and first["id"] == photo.expected_object_id
                    ),
                    "top3_correct": bool(
                        photo.expected_object_id
                        and photo.expected_object_id in [c["id"] for c in candidates]
                    ),
                    "warnings": body.get("warnings", []),
                    "timings": body.get("timings", {}),
                    "budget_exceeded": "INFERENCE_BUDGET_EXCEEDED"
                    in body.get("warnings", []),
                    "inference_ms": body.get("inferenceMs"),
                    "frame_sha256": hashlib.sha256(frame).hexdigest(),
                    "reference_version": body.get("reference_version"),
                }
                rows.append(row)
                print(f"{photo.id}: HTTP {status}", flush=True)
                await asyncio.sleep(0.26)
        end_version, _, _ = await references.snapshot()
        gates = {
            "references_unchanged": end_version == version,
            "code_unchanged": pipeline_hash() == frozen_code,
            "manifest_unchanged": args.manifest.read_bytes() == manifest_bytes,
            "photos_unchanged": inspect_photos(manifest.photos, args.manifest.parent)
            == (raw, normalized),
            "all_requests_valid": all(r["http_status"] == 200 for r in rows),
        }
        report: dict[str, Any] = {
            "created_at": datetime.now(UTC).isoformat(),
            "purpose": manifest.purpose,
            "phase": args.phase,
            "mode": args.mode,
            "browser": args.browser,
            "manifest_hash": manifest_hash,
            "photo_hashes": raw,
            "normalized_photo_hashes": normalized,
            "pipeline": PIPELINE_VERSION
            if args.mode != "legacy"
            else "legacy-per-view-geometry",
            "pipeline_hash": frozen_code,
            "reference_signature": references.reference_signature,
            "reference_version": version,
            "model_sha256": engine.catalog["model"]["sha256"],
            "policy": engine.catalog["policy"],
            "reference_readiness": reference_readiness,
            "startup_seconds": startup_seconds,
            "model_warm_ms": engine.warm_ms,
            "summary": summarize(rows),
            "rows": rows,
            "integrity_gates": gates,
            "by_type": {
                kind: summarize([r for r in rows if r["object_type"] == kind])
                for kind in sorted({r["object_type"] for r in rows})
            },
            "by_object": {
                oid: summarize([r for r in rows if r["expected_object_id"] == oid])
                for oid in sorted(
                    {r["expected_object_id"] for r in rows if r["expected_object_id"]}
                )
            },
            "suggestions_are_static_simulations": True,
            "tracking_measured": False,
            "physical_phone_measured": False,
            "automatic_identification": False,
        }
        if args.phase == "calibrate":
            if not all(gates.values()):
                raise ValueError("Calibration replay failed integrity checks")
            thresholds, fits = fit_profiles(rows)
            fitted = CameraCalibration(
                pipeline_hash=frozen_code,
                model_sha256=report["model_sha256"],
                reference_signature=references.reference_signature,
                object_regions=settings.live_camera_object_regions,
                inference_budget_ms=settings.live_camera_inference_budget_ms,
                manifest_hash=manifest_hash,
                calibration_photo_hashes={p.id: raw[p.id] for p in selected},
                normalized_photo_hashes=normalized,
                calibration_capture_groups=sorted({p.capture_group for p in selected}),
                profiles=thresholds,
                fit_results=fits,
            )
            fitted.artifact_hash = digest(fitted.model_dump(exclude={"artifact_hash"}))
            args.policy_output.write_text(fitted.model_dump_json(indent=2) + "\n")
            report["calibration_artifact_hash"] = fitted.artifact_hash
            report["fit_results"] = fits
        historical = historical_image_hashes()
        summary = report["summary"]
        positive_counts = Counter(
            r["expected_object_id"] for r in rows if r["expected_object_id"]
        )
        full = [r for r in rows if r["expected_object_id"] and r["category"] == "full"]
        partial = [
            r for r in rows if r["expected_object_id"] and r["category"] == "partial"
        ]
        report["release_gates"] = {
            "new_reviewed_holdout": manifest.purpose == "acceptance"
            and manifest.independence_reviewed
            and all(p.independent for p in selected)
            and not historical.intersection(
                set(raw[p.id] for p in selected)
                | set(normalized[p.id] for p in selected)
            ),
            "representative_types": {"painting", "sculpture", "building"}.issubset(
                report["by_type"]
            ),
            "twenty_positive_identities": len(positive_counts) >= 20,
            "full_view_top3_90_percent": len(full) >= 20
            and sum(r["top3_correct"] for r in full) / len(full) >= 0.9,
            "partial_view_top3_80_percent": len(partial) >= 40
            and sum(r["top3_correct"] for r in partial) / len(partial) >= 0.8,
            "reference_view_coverage": all(
                c["ready"] for c in reference_readiness.values()
            ),
            "suggestion_precision_98_percent": summary["suggestion_precision"]["total"]
            >= 30
            and summary["suggestion_precision"]["rate"] >= 0.98,
            "correct_suggestion_coverage_50_percent": summary[
                "correct_suggestion_coverage"
            ]["rate"]
            is not None
            and summary["correct_suggestion_coverage"]["rate"] >= 0.5,
            "zero_unknown_false_suggestions": summary["negative_count"] >= 30
            and summary["negative_false_suggestions"] == 0,
            "warm_request_p95_two_seconds": summary["p95_request_seconds"] is not None
            and summary["p95_request_seconds"] <= 2,
            "bounded_model_calls": summary["max_model_calls"] <= 3,
            "physical_phone_and_tracking": False,
        }
        report["release_ready"] = all(gates.values()) and all(
            report["release_gates"].values()
        )
        return report
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("legacy", "adaptive", "regions"), default="adaptive"
    )
    parser.add_argument(
        "--phase", choices=("replay", "calibrate", "validate"), default="replay"
    )
    parser.add_argument(
        "--split", choices=("all", "calibration", "evaluation"), default="all"
    )
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="webkit")
    parser.add_argument("--budget-ms", type=int, default=1500)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--policy-output", type=Path)
    args = parser.parse_args()
    if args.output.exists() or (args.policy_output and args.policy_output.exists()):
        parser.error("Refusing to overwrite frozen evidence")
    if not 250 <= args.budget_ms <= 2000:
        parser.error("Budget must be 250–2000 ms")
    if args.phase == "calibrate" and (
        args.mode == "legacy" or not args.policy_output or args.calibration
    ):
        parser.error("Calibration requires adaptive/regions mode and --policy-output")
    if args.phase == "validate" and (not args.calibration or args.mode == "legacy"):
        parser.error("Validation requires adaptive/regions mode and --calibration")
    try:
        report = asyncio.run(run(args))
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    except Exception as exc:
        print(
            f"CAMERA_PIPELINE_FAILED ({type(exc).__name__}): "
            "check frozen inputs, model/browser "
            "and Atlas readiness."
        )
        raise SystemExit(2) from None
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "integrity": report["integrity_gates"],
                "release_ready": report["release_ready"],
            },
            indent=2,
        )
    )
    # A diagnostic can finish successfully without declaring release readiness.
    raise SystemExit(
        0
        if args.phase != "validate" and all(report["integrity_gates"].values())
        else 0
        if report["release_ready"]
        else 1
    )


if __name__ == "__main__":
    main()
