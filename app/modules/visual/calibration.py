"""Fit on calibration captures and validate a frozen policy on held-out captures."""

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.modules.visual.benchmark import (
    BenchmarkPolicy,
    Photo,
    Thresholds,
    benchmark,
    dataset_coverage,
    inspect_photos,
)
from app.modules.visual.features import ImageEncoder, VisualFailure, digest
from app.modules.visual.repository import VisualRepository


class Calibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["visual-calibration-v1"] = "visual-calibration-v1"
    created_at: str
    manifest_hash: str
    photo_hashes: dict[str, str]
    normalized_photo_hashes: dict[str, str]
    reference_sets: dict[str, list[str]]
    policy: BenchmarkPolicy
    methods: dict[str, Any]
    artifact_hash: str = ""

    def verify(self) -> None:
        if self.artifact_hash != digest(self.model_dump(exclude={"artifact_hash"})):
            raise VisualFailure("CALIBRATION_ARTIFACT_CHANGED")
        if set(self.policy.methods) != {"global", "regional", "local"}:
            raise VisualFailure("BENCHMARK_POLICY_MISMATCH")


def audit_dataset(photos: list[Photo], root: Path) -> dict[str, Any]:
    raw, normalized = inspect_photos(photos, root)
    splits = {
        split: dataset_coverage(photos, split)
        for split in ("calibration", "evaluation")
    }
    calibration = splits["calibration"]
    ready = (
        calibration["gates"]["independent_photos"]
        and calibration["positive_photos"] > 0
        and calibration["negative_photos"] > 0
    )
    return {
        "manifest_hash": digest([p.model_dump() for p in photos]),
        "photo_hashes": raw,
        "normalized_photo_hashes": normalized,
        "splits": splits,
        "ready_for_calibration": ready,
        "ready_for_held_out_validation": ready
        and splits["evaluation"]["evaluation_dataset_ready"],
        "reference_coverage": "Unchecked locally; calibration checks Atlas references.",
        "provenance_review": "Identity and independence require human review.",
        "automatic_api_enabled": False,
        "phase5_complete": False,
    }


def fit_thresholds(
    rows: list[dict[str, Any]], minimum: float
) -> tuple[Thresholds, dict[str, Any]]:
    """Bounded deterministic grid: maximize correct matches with zero false matches.

    Keep the retrieval floor fixed. Prefer stricter thresholds on ties. No viable
    operating point disables this method rather than inventing a confidence score.
    """
    if not rows or not all(row["independent"] for row in rows):
        raise VisualFailure("CALIBRATION_REQUIRES_INDEPENDENT_PHOTOS")
    positives = sum(row["expected_object_id"] is not None for row in rows)
    negatives = len(rows) - positives
    if not positives or not negatives:
        raise VisualFailure("CALIBRATION_REQUIRES_POSITIVES_AND_NEGATIVES")
    observations = []
    for row in rows:
        scores = row["scores"]
        if scores:
            first = scores[0]
            second = scores[1]["score"] if len(scores) > 1 else -1.0
            observations.append(
                (
                    first["score"],
                    first["score"] - second,
                    first["object_id"] == row["expected_object_id"],
                )
            )
    best = (0, 1.0, 2.0)
    tested = 0
    for score_int in range(math.ceil(minimum * 100), 101):
        automatic = score_int / 100
        for margin_int in range(0, 101, 2):
            margin = margin_int / 100
            correct = 0
            tested += 1
            for score, gap, is_correct in observations:
                if score >= automatic and gap >= margin:
                    if not is_correct:
                        break
                    correct += 1
            else:
                if correct and (correct, automatic, margin) > best:
                    best = correct, automatic, margin
    count, automatic, margin = best
    policy = Thresholds(
        minimum=minimum, automatic=automatic, margin=margin, automatic_enabled=count > 0
    )
    return policy, {
        "status": "fitted" if count else "disabled_no_safe_operating_point",
        "positive_photos": positives,
        "negative_photos": negatives,
        "automatic_matches": count,
        "incorrect_automatic_matches": 0,
        "automatic_precision": 1.0 if count else None,
        "positive_automatic_coverage": count / positives,
        "grid_points_tested": tested,
        "selection_rule": (
            "Maximum correct matches with zero incorrect matches; "
            "stricter score then margin on ties"
        ),
        "grid": {"score_step": 0.01, "margin_step": 0.02, "maximum_margin": 1.0},
    }


async def calibrate(
    repository: VisualRepository,
    encoder: ImageEncoder,
    photos: list[Photo],
    root: Path,
    seed: BenchmarkPolicy,
) -> Calibration:
    audit = audit_dataset(photos, root)
    if not audit["ready_for_calibration"]:
        raise VisualFailure("CALIBRATION_DATASET_INCOMPLETE")
    report = await benchmark(
        repository,
        encoder,
        photos,
        root,
        seed,
        "calibration",
        expected_photo_hashes=audit["photo_hashes"],
        expected_normalized_hashes=audit["normalized_photo_hashes"],
    )
    thresholds, methods = {}, {}
    for method, result in report["methods"].items():
        thresholds[method], methods[method] = fit_thresholds(
            result["rows"], seed.methods[method].minimum
        )
    artifact = Calibration(
        created_at=datetime.now(UTC).isoformat(),
        manifest_hash=report["manifest_hash"],
        photo_hashes=report["photo_hashes"],
        normalized_photo_hashes=report["normalized_photo_hashes"],
        reference_sets=report["reference_sets"],
        policy=BenchmarkPolicy(
            model=encoder.model, preprocessing=encoder.preprocessing, methods=thresholds
        ),
        methods=methods,
    )
    artifact.artifact_hash = digest(artifact.model_dump(exclude={"artifact_hash"}))
    return artifact


async def validate_calibration(
    repository: VisualRepository,
    encoder: ImageEncoder,
    photos: list[Photo],
    root: Path,
    calibration: Calibration,
) -> dict[str, Any]:
    calibration.verify()
    if calibration.manifest_hash != digest([p.model_dump() for p in photos]):
        raise VisualFailure("CALIBRATION_MANIFEST_CHANGED")
    report = await benchmark(
        repository,
        encoder,
        photos,
        root,
        calibration.policy,
        "evaluation",
        calibration_verified=True,
        expected_reference_sets=calibration.reference_sets,
        expected_photo_hashes=calibration.photo_hashes,
        expected_normalized_hashes=calibration.normalized_photo_hashes,
    )
    report["calibration_artifact_hash"] = calibration.artifact_hash
    report["dataset_audit"] = audit_dataset(photos, root)
    report["ready_for_regional_api_validation"] = report["methods"]["regional"][
        "recognition_gates_passed"
    ]
    report["automatic_api_enabled"] = False
    report["remaining_gate"] = (
        "Validate the frozen regional policy through the API and review provenance "
        "before enabling automatic identification."
    )
    return report
