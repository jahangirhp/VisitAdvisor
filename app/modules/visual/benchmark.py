"""Reproducible, capture-separated comparison; synthetic photos cannot pass gates."""

import asyncio
import hashlib
import math
import mimetypes
import platform
import resource
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import validate_id
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.recognition.images import MAX_IMAGE_BYTES, prepare_image
from app.modules.visual.features import ImageEncoder, VisualFailure, digest, rank
from app.modules.visual.repository import VisualRepository


class Photo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    path: str
    location_id: str
    expected_object_id: str | None
    capture_group: str = Field(min_length=1)
    split: Literal["calibration", "evaluation"]
    provenance: str = Field(min_length=1)
    independent: bool
    category: Literal[
        "full",
        "partial",
        "outside_reference",
        "similar_object",
        "wrong_location",
        "uninformative",
    ]
    object_type: str = Field(min_length=1)
    viewpoint: str = Field(min_length=1)
    visible_fraction: float | None = Field(default=None, gt=0, le=1)


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum: float = Field(ge=-1, le=1)
    automatic: float = Field(ge=-1, le=1)
    margin: float = Field(ge=0, le=2)
    automatic_enabled: bool = True


class BenchmarkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    preprocessing: str
    regional_global_weight: float = Field(default=0.0, ge=0, le=1)
    methods: dict[Literal["global", "regional", "local"], Thresholds]


def select(candidates: list[dict[str, Any]], policy: Thresholds) -> str | None:
    if (
        not policy.automatic_enabled
        or not candidates
        or candidates[0]["score"] < max(policy.automatic, policy.minimum)
    ):
        return None
    second = candidates[1]["score"] if len(candidates) > 1 else -1.0
    return (
        str(candidates[0]["object_id"])
        if candidates[0]["score"] - second >= policy.margin
        else None
    )


def load_photo(root: Path, path: str) -> tuple[bytes, str]:
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise VisualFailure("BENCHMARK_PATH_OUTSIDE_MANIFEST_DIRECTORY")
    with resolved.open("rb") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    clean = prepare_image(data, mimetypes.guess_type(resolved.name)[0])
    return clean, hashlib.sha256(data).hexdigest()


def validate_manifest(photos: list[Photo]) -> None:
    if not 1 <= len(photos) <= 3000 or len({p.id for p in photos}) != len(photos):
        raise VisualFailure("INVALID_BENCHMARK_MANIFEST")
    if len({p.location_id for p in photos}) > 5:
        raise VisualFailure("BENCHMARK_LOCATION_LIMIT")
    groups: dict[str, str] = {}
    object_types: dict[str, str] = {}
    for photo in photos:
        validate_id(photo.location_id)
        if photo.expected_object_id:
            validate_id(photo.expected_object_id)
            if (
                object_types.setdefault(photo.expected_object_id, photo.object_type)
                != photo.object_type
            ):
                raise VisualFailure("BENCHMARK_OBJECT_TYPE_CONFLICT")
        if (photo.category in ("full", "partial")) != (
            photo.expected_object_id is not None
        ):
            raise VisualFailure("INVALID_BENCHMARK_EXPECTATION")
        if groups.setdefault(photo.capture_group, photo.split) != photo.split:
            raise VisualFailure("BENCHMARK_CAPTURE_LEAKAGE")


def inspect_photos(
    photos: list[Photo], root: Path
) -> tuple[dict[str, str], dict[str, str]]:
    """Check every split before fitting, including metadata-only duplicate images."""
    validate_manifest(photos)
    raw_hashes, normalized_hashes = {}, {}
    seen: dict[str, Photo] = {}
    for photo in photos:
        clean, raw = load_photo(root, photo.path)
        normalized = hashlib.sha256(clean).hexdigest()
        for value in {raw, normalized}:
            if value in seen:
                code = (
                    "BENCHMARK_IMAGE_LEAKAGE"
                    if seen[value].split != photo.split
                    else "BENCHMARK_DUPLICATE_IMAGE"
                )
                raise VisualFailure(code)
            seen[value] = photo
        raw_hashes[photo.id], normalized_hashes[photo.id] = raw, normalized
    return raw_hashes, normalized_hashes


def dataset_coverage(
    photos: list[Photo], split: str, independent_ids: set[str] | None = None
) -> dict[str, Any]:
    selected = [p for p in photos if p.split == split]
    eligible = [
        p
        for p in selected
        if p.independent and (independent_ids is None or p.id in independent_ids)
    ]
    objects = []
    for object_id in sorted(
        {p.expected_object_id for p in eligible if p.expected_object_id}
    ):
        views = [p for p in eligible if p.expected_object_id == object_id]
        full = {p.capture_group for p in views if p.category == "full"}
        partial = {p.capture_group for p in views if p.category == "partial"}
        objects.append(
            {
                "object_id": object_id,
                "object_type": views[0].object_type,
                "location_id": views[0].location_id,
                "full_capture_groups": sorted(full),
                "partial_capture_groups": sorted(partial),
                "missing_full_captures": int(not full - partial),
                "missing_partial_captures": max(0, 2 - len(partial)),
            }
        )
    negatives = [p for p in eligible if p.expected_object_id is None]
    required_negatives = {
        "outside_reference",
        "similar_object",
        "wrong_location",
        "uninformative",
    }
    required_types = {"painting", "sculpture", "building"}
    types = {row["object_type"] for row in objects}
    categories = {p.category for p in negatives}
    gates = {
        "independent_photos": bool(selected) and len(eligible) == len(selected),
        "twenty_objects": len(objects) >= 20,
        "full_and_partial_captures": bool(objects)
        and all(
            not r["missing_full_captures"] and not r["missing_partial_captures"]
            for r in objects
        ),
        "thirty_negatives": len(negatives) >= 30,
        "negative_categories": required_negatives.issubset(categories),
        "object_types": required_types.issubset(types),
    }
    return {
        "photos": len(selected),
        "independent_photos": len(eligible),
        "positive_photos": len(eligible) - len(negatives),
        "negative_photos": len(negatives),
        "positive_objects": len(objects),
        "objects": objects,
        "missing_objects": max(0, 20 - len(objects)),
        "missing_negatives": max(0, 30 - len(negatives)),
        "missing_negative_categories": sorted(required_negatives - categories),
        "missing_object_types": sorted(required_types - types),
        "excluded_photo_ids": sorted(p.id for p in selected if p not in eligible),
        "gates": gates,
        "evaluation_dataset_ready": all(gates.values()),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [r for r in rows if r["expected_object_id"] is not None]
    negative = [r for r in rows if r["expected_object_id"] is None]
    automatic = [r for r in rows if r["selected"] is not None]
    correct = [r for r in automatic if r["selected"] == r["expected_object_id"]]
    categories: dict[str, Any] = {}
    for category in sorted({r["category"] for r in rows}):
        subset = [r for r in rows if r["category"] == category]
        categories[category] = {
            "count": len(subset),
            "top3_correct": sum(
                r["expected_object_id"] in r["candidates"]
                for r in subset
                if r["expected_object_id"]
            ),
            "false_automatic": sum(
                r["selected"] is not None and r["selected"] != r["expected_object_id"]
                for r in subset
            ),
        }
    return {
        "count": len(rows),
        "positives": len(positive),
        "negatives": len(negative),
        "top3_correct": sum(
            r["expected_object_id"] in r["candidates"] for r in positive
        ),
        "automatic": len(automatic),
        "automatic_correct": len(correct),
        "automatic_precision": len(correct) / len(automatic) if automatic else None,
        "positive_automatic_coverage": sum(r["selected"] is not None for r in positive)
        / len(positive)
        if positive
        else None,
        "abstention_rate": 1 - len(automatic) / len(rows) if rows else None,
        "negative_false_automatic": sum(r["selected"] is not None for r in negative),
        "categories": categories,
    }


async def benchmark(
    repository: VisualRepository,
    encoder: ImageEncoder,
    photos: list[Photo],
    root: Path,
    policy: BenchmarkPolicy,
    split: str,
    *,
    calibration_verified: bool = False,
    expected_reference_sets: dict[str, list[str]] | None = None,
    expected_photo_hashes: dict[str, str] | None = None,
    expected_normalized_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_manifest(photos)
    if (
        policy.model != encoder.model
        or policy.preprocessing != encoder.preprocessing
        or set(policy.methods) != {"global", "regional", "local"}
    ):
        raise VisualFailure("BENCHMARK_POLICY_MISMATCH")
    # Validate all captures, including the other split, before any inference.
    photo_hashes, normalized_hashes = inspect_photos(photos, root)
    if (
        expected_photo_hashes is not None
        and photo_hashes != expected_photo_hashes
        or expected_normalized_hashes is not None
        and normalized_hashes != expected_normalized_hashes
    ):
        raise VisualFailure("BENCHMARK_PHOTO_CHANGED")
    records_by_location = {}
    totals = {}
    for location_id in {p.location_id for p in photos}:
        await LocationService(LocationRepository(repository.db)).get(location_id)
        records, total = await repository.active(
            location_id, encoder.model, encoder.preprocessing
        )
        records_by_location[location_id] = records
        totals[location_id] = {"covered": len(records), "total": total}
    reference_sets = {
        loc: sorted(r["set_id"] for r in records)
        for loc, records in records_by_location.items()
    }
    if (
        expected_reference_sets is not None
        and reference_sets != expected_reference_sets
    ):
        raise VisualFailure("BENCHMARK_REFERENCE_SET_CHANGED")
    reference_hashes = {
        value
        for records in records_by_location.values()
        for r in records
        for v in r["views"]
        for value in (v["image_hash"], v.get("normalized_image_hash"))
        if value
    }
    rows: dict[str, list[dict[str, Any]]] = {method: [] for method in policy.methods}
    start = time.perf_counter()
    selected_photos = [p for p in photos if p.split == split]
    if not selected_photos:
        raise VisualFailure("BENCHMARK_SPLIT_EMPTY")
    for photo in selected_photos:
        records = records_by_location[photo.location_id]
        if photo.expected_object_id and photo.expected_object_id not in {
            r["object_id"] for r in records
        }:
            raise VisualFailure("BENCHMARK_POSITIVE_OUTSIDE_REFERENCE_COVERAGE")
        independent = (
            photo.independent
            and photo_hashes[photo.id] not in reference_hashes
            and normalized_hashes[photo.id] not in reference_hashes
        )
        tick = time.perf_counter()
        data, current_hash = load_photo(root, photo.path)
        if current_hash != photo_hashes[photo.id]:
            raise VisualFailure("BENCHMARK_PHOTO_CHANGED")
        query = await asyncio.to_thread(encoder.describe, data, regions=False)
        encoding_seconds = time.perf_counter() - tick
        for method, thresholds in policy.methods.items():
            tick = time.perf_counter()
            candidates = await asyncio.to_thread(
                rank,
                query,
                records,
                method,
                regional_global_weight=policy.regional_global_weight,
            )
            selected_id = select(candidates, thresholds)
            rows[method].append(
                {
                    "id": photo.id,
                    "expected_object_id": photo.expected_object_id,
                    "category": photo.category,
                    "object_type": photo.object_type,
                    "capture_group": photo.capture_group,
                    "independent": independent,
                    "candidates": [
                        c["object_id"]
                        for c in candidates
                        if c["score"] >= thresholds.minimum
                    ],
                    "scores": candidates,
                    "selected": selected_id,
                    "seconds": encoding_seconds + time.perf_counter() - tick,
                }
            )
    methods = {}
    for method_name, results in rows.items():
        summary = summarize(results)
        by_type = {
            kind: summarize([r for r in results if r["object_type"] == kind])
            for kind in sorted({r["object_type"] for r in results})
        }
        full = summary["categories"].get("full", {"count": 0, "top3_correct": 0})
        partial = summary["categories"].get("partial", {"count": 0, "top3_correct": 0})
        coverage = dataset_coverage(
            photos, split, {r["id"] for r in results if r["independent"]}
        )
        gates = {
            "calibration_policy_verified": calibration_verified,
            "calibration_split_present": any(p.split == "calibration" for p in photos),
            "held_out_independent_photos": split == "evaluation"
            and all(r["independent"] for r in results),
            "minimum_photo_coverage": all(
                coverage["gates"][k]
                for k in (
                    "twenty_objects",
                    "full_and_partial_captures",
                    "thirty_negatives",
                )
            ),
            "object_types": coverage["gates"]["object_types"],
            "negative_categories": {
                "outside_reference",
                "similar_object",
                "wrong_location",
                "uninformative",
            }.issubset(summary["categories"]),
            "full_top3": full["count"] > 0
            and full["top3_correct"] / full["count"] >= 0.9,
            "partial_top3": partial["count"] > 0
            and partial["top3_correct"] / partial["count"] >= 0.8,
            "automatic_precision": summary["automatic"] >= 30
            and (summary["automatic_precision"] or 0) >= 0.98,
            "automatic_coverage": (summary["positive_automatic_coverage"] or 0) >= 0.5,
            "negative_false_matches": summary["negatives"] > 0
            and summary["negative_false_automatic"] == 0,
        }
        methods[method_name] = {
            **summary,
            "by_object_type": by_type,
            "gates": gates,
            "recognition_gates_passed": all(gates.values()),
            "rows": results,
            "offline_p95_seconds": sorted(r["seconds"] for r in results)[
                math.ceil(0.95 * len(results)) - 1
            ],
        }
    return {
        "model": encoder.model,
        "preprocessing": encoder.preprocessing,
        "policy": policy.model_dump(),
        "policy_hash": digest(policy.model_dump()),
        "manifest_hash": digest([p.model_dump() for p in photos]),
        "split": split,
        "photo_hashes": photo_hashes,
        "normalized_photo_hashes": normalized_hashes,
        "reference_sets": reference_sets,
        "coverage": totals,
        "methods": methods,
        "elapsed_seconds": time.perf_counter() - start,
        "hardware": {"machine": platform.machine(), "processor": platform.processor()},
        "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "latency_scope": "Offline encoding/comparison; excludes HTTP and Atlas",
        "phase5_complete": False,
        "remaining_gate": "Independent API validation and object-type review required.",
    }
