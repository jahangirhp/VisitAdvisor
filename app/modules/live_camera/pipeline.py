"""Experimental object-focused, type-aware recognition with bounded CPU work."""

from time import perf_counter
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageStat

from app.modules.live_camera.geometry import describe_local
from app.modules.live_camera.regions import FULL_FRAME, crop, proposals
from app.modules.visual.features import geometric_evidence

if TYPE_CHECKING:
    from app.modules.live_camera.inference import CameraRecognizer

PIPELINE_VERSION = "object-regions-types-v5"
PLANAR_TYPES = {"painting", "drawing", "print", "photograph"}


def profile(object_type: str) -> str:
    return "planar" if object_type.lower().strip() in PLANAR_TYPES else "multiview"


def view_coverage(obj: dict[str, Any]) -> dict[str, Any]:
    from app.modules.visual.features import digest

    views = obj["views"]
    hashes = {view.get("image_hash") or digest(view["vectors"]) for view in views}
    labels = {
        view.get("viewpoint", "").strip().lower()
        for view in views
        if view.get("viewpoint")
    }
    kind = profile(obj.get("type", "other"))
    return {
        "profile": kind,
        "distinct_images": len(hashes),
        "distinct_viewpoints": len(labels),
        "ready": bool(hashes)
        if kind == "planar"
        else len(hashes) >= 2 and len(labels) >= 2,
    }


def embedding(engine: "CameraRecognizer", image: Image.Image) -> Any:
    import numpy as np

    from app.modules.live_camera.inference import CameraFailure

    # Preserve frame detail for local matching; the encoder still needs 224 RGB.
    encoded = image.resize((224, 224), Image.Resampling.BICUBIC)
    pixels = np.asarray(encoded, dtype=np.float32) / 255
    means = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    stds = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = np.ascontiguousarray(((pixels - means) / stds).transpose(2, 0, 1)[None])
    vector = np.asarray(
        engine.session.run(["embedding"], {"pixel_values": tensor})[0], dtype=np.float32
    ).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.shape != (384,) or not np.isfinite(vector).all() or norm <= 0:
        raise CameraFailure("RECOGNITION_UNAVAILABLE")
    return vector / norm


def recognize(
    engine: "CameraRecognizer", image: Image.Image, started: float
) -> dict[str, Any]:
    base = {
        "version": "camera-server-v1",
        "location_id": engine.catalog["location"]["id"],
        "reference_version": engine.reference_version,
        "automatic_identification": False,
        "pipeline": PIPELINE_VERSION,
    }
    timings = {"proposal_ms": 0.0, "encoder_ms": 0.0, "verification_ms": 0.0}
    calls = 0
    if max(ImageStat.Stat(image).stddev) < 2:
        return {
            **base,
            "candidates": [],
            "decision": "unreadable",
            "suggestion_allowed": False,
            "warnings": ["FRAME_HAS_NO_DETAIL"],
            "inferenceMs": (perf_counter() - started) * 1000,
            "timings": {**timings, "model_calls": 0},
        }
    policy = engine.catalog["policy"]
    budget_ms = float(policy.get("inference_budget_ms", 1500))
    candidate_map: dict[str, dict[str, Any]] = {}
    proposal_started = perf_counter()
    boxes = (
        [FULL_FRAME] + proposals(image)
        if policy.get("object_regions")
        else [FULL_FRAME]
    )
    timings["proposal_ms"] = (perf_counter() - proposal_started) * 1000
    budget_exceeded = False
    for box in boxes:
        if (perf_counter() - started) * 1000 >= budget_ms:
            budget_exceeded = True
            break
        region = image if box == FULL_FRAME else crop(image, box)
        encoder_started = perf_counter()
        vector = embedding(engine, region)
        calls += 1
        timings["encoder_ms"] += (perf_counter() - encoder_started) * 1000
        if (perf_counter() - started) * 1000 >= budget_ms:
            budget_exceeded = True
            break
        # A view is a source photograph, not one of its derived regional vectors.
        shortlisted = []
        for oid, views in engine.views:
            similarities = [view @ vector for view in views]
            scores = [0.75 * float(s[0]) + 0.25 * float(s.max()) for s in similarities]
            view_index = max(range(len(scores)), key=scores.__getitem__)
            shortlisted.append((scores[view_index], oid, view_index, scores))
        shortlisted.sort(key=lambda row: (-row[0], row[1]))
        local = None
        verification_started = perf_counter()
        for score, oid, index, view_scores in shortlisted:
            if (perf_counter() - started) * 1000 >= budget_ms:
                budget_exceeded = True
                break
            coverage = engine.reference_coverage[oid]
            if coverage["profile"] == "planar":
                if local is None:
                    local = describe_local(region)
                # Local details retrieve exact planar instances independently of
                # whole-scene embeddings. Never discard an object or viewpoint
                # solely because its embedding score was outside the shortlist.
                score = 0.0
                for view_index, reference in enumerate(engine.local_views[oid]):
                    if (perf_counter() - started) * 1000 >= budget_ms:
                        budget_exceeded = True
                        break
                    evidence = geometric_evidence(local, reference)
                    verified_score = (
                        evidence["score"] if evidence["score"] >= 0.4 else 0.0
                    )
                    if verified_score > score:
                        score, index = verified_score, view_index
            candidate = {
                "id": oid,
                "score": max(0.0, min(1.0, score)),
                "profile": coverage["profile"],
                "view_index": index,
                "reference_views": coverage["distinct_images"],
                "coverage_ready": coverage["ready"],
                "bbox": list(box),
                "verification": "planar_geometry"
                if coverage["profile"] == "planar"
                else "multiview_embeddings",
                "view_scores": view_scores,
            }
            if (
                oid not in candidate_map
                or candidate["score"] > candidate_map[oid]["score"]
            ):
                candidate_map[oid] = candidate
        timings["verification_ms"] += (perf_counter() - verification_started) * 1000
    elapsed = (perf_counter() - started) * 1000
    budget_exceeded |= elapsed > budget_ms
    candidates = sorted(candidate_map.values(), key=lambda c: (-c["score"], c["id"]))[
        :3
    ]
    first = candidates[0] if candidates else None
    thresholds = policy.get("profiles", {}).get(first["profile"], {}) if first else {}
    floor = thresholds.get("floor", policy["candidate_floor"])
    margin = thresholds.get("margin", policy["candidate_margin"])
    # New score semantics need a bound, fitted preview policy. Uncalibrated
    # candidates may be inspected, but cannot establish a stable suggestion.
    enabled = thresholds.get("enabled", False)
    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
    allowed = bool(
        first
        and enabled
        and first["coverage_ready"]
        and first["score"] >= floor
        and first["score"] - second >= margin
        and not budget_exceeded
    )
    warnings = ["ADAPTIVE_PIPELINE_EXPERIMENTAL"]
    if not policy.get("profiles"):
        warnings.append("CAMERA_CALIBRATION_REQUIRED")
    if first and not first["coverage_ready"]:
        warnings.append("REFERENCE_VIEW_COVERAGE_INCOMPLETE")
    if budget_exceeded:
        warnings.append("INFERENCE_BUDGET_EXCEEDED")
    engine.check_fresh()
    return {
        **base,
        "candidates": candidates,
        "decision": "deferred"
        if budget_exceeded
        else "candidate"
        if allowed
        else "ambiguous"
        if first and first["score"] >= 0.45
        else "no_match",
        "suggestion_allowed": allowed,
        "warnings": warnings,
        "inferenceMs": round(elapsed, 2),
        "timings": {**timings, "model_calls": calls, "proposals": len(boxes) - 1},
    }
