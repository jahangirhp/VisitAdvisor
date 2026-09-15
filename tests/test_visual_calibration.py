"""Engineered fixtures test the workflow; they are not recognition accuracy evidence."""

import asyncio
import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image, PngImagePlugin

from app.modules.visual.__main__ import run, write_result
from app.modules.visual.benchmark import (
    BenchmarkPolicy,
    Thresholds,
    benchmark,
    dataset_coverage,
    inspect_photos,
    load_photo,
    select,
)
from app.modules.visual.calibration import (
    audit_dataset,
    calibrate,
    fit_thresholds,
    validate_calibration,
)
from app.modules.visual.features import VisualFailure
from tests.test_visual import features, photo, picture, prepare_one, setup


@pytest.fixture
def calibration_setup():
    return setup.__wrapped__()


def row(expected, scores, independent=True):
    return {
        "expected_object_id": expected,
        "independent": independent,
        "scores": [{"object_id": obj, "score": score} for obj, score in scores],
    }


def test_calibration_rejects_false_matches_even_with_high_similarity():
    rows = [
        row("a", [("a", 0.8), ("b", 0.2)]),
        row("b", [("a", 0.95), ("b", 0.94)]),
        row(None, [("a", 0.99), ("b", 0.985)]),
    ]
    thresholds, summary = fit_thresholds(rows, 0.45)
    assert thresholds.minimum == 0.45
    assert [select(r["scores"], thresholds) for r in rows] == ["a", None, None]
    assert summary["automatic_matches"] == 1
    assert fit_thresholds(list(reversed(rows)), 0.45) == (thresholds, summary)


def test_no_safe_threshold_explicitly_disables_automatic_matching():
    thresholds, result = fit_thresholds(
        [row("a", [("a", 1.0)]), row(None, [("a", 1.0)])], 0.45
    )
    assert not thresholds.automatic_enabled
    assert select([{"object_id": "a", "score": 1.0}], thresholds) is None
    assert result["automatic_precision"] is None


@pytest.mark.parametrize(
    "rows",
    [
        [row("a", [("a", 0.9)])],
        [row(None, [("a", 0.9)])],
        [row("a", [("a", 0.9)], False), row(None, [])],
    ],
)
def test_inadequate_calibration_photos_cannot_produce_thresholds(rows):
    with pytest.raises(VisualFailure):
        fit_thresholds(rows, 0.45)


def test_normalized_duplicates_cannot_cross_splits_or_inflate_counts(tmp_path):
    image = Image.new("RGB", (30, 30), "red")
    for i in range(2):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("comment", str(i))
        image.save(tmp_path / f"{i}.png", pnginfo=metadata)
    photos = [photo(id=str(i), path=f"{i}.png", capture_group=str(i)) for i in range(2)]
    with pytest.raises(VisualFailure, match="BENCHMARK_DUPLICATE_IMAGE"):
        inspect_photos(photos, tmp_path)
    photos[0].split = "calibration"
    with pytest.raises(VisualFailure, match="BENCHMARK_IMAGE_LEAKAGE"):
        inspect_photos(photos, tmp_path)


def test_metadata_variant_of_reference_is_not_independent(calibration_setup, tmp_path):
    db, repository, encoder = calibration_setup
    asyncio.run(prepare_one(calibration_setup))

    with Image.open(io.BytesIO(picture())) as image:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("comment", "Different bytes, same reference pixels")
        image.save(tmp_path / "variant.png", pnginfo=metadata)
    policy = BenchmarkPolicy(
        model=encoder.model,
        preprocessing=encoder.preprocessing,
        methods={
            m: Thresholds(minimum=0.45, automatic=0.8, margin=0.1)
            for m in ("global", "regional", "local")
        },
    )
    report = asyncio.run(
        benchmark(
            repository,
            encoder,
            [
                photo(
                    path="variant.png",
                    independent=True,
                    expected_object_id=str(db["objects"].documents[0]["_id"]),
                )
            ],
            tmp_path,
            policy,
            "evaluation",
        )
    )
    assert not report["methods"]["regional"]["rows"][0]["independent"]
    assert not report["methods"]["regional"]["gates"]["held_out_independent_photos"]


def test_coverage_requires_three_distinct_captures_and_other_object_types():
    photos = []
    for i in range(20):
        for j, category in enumerate(("full", "partial", "partial")):
            photos.append(
                photo(
                    id=f"{i}-{j}",
                    capture_group=f"{i}-{j}",
                    category=category,
                    expected_object_id=f"{i + 1:024x}",
                    independent=True,
                    object_type=("painting", "sculpture", "building")[i % 3],
                )
            )
    for i in range(30):
        photos.append(
            photo(
                id=f"n{i}",
                capture_group=f"n{i}",
                independent=True,
                expected_object_id=None,
                category=(
                    "outside_reference",
                    "similar_object",
                    "wrong_location",
                    "uninformative",
                )[i % 4],
            )
        )
    assert dataset_coverage(photos, "evaluation")["evaluation_dataset_ready"]
    photos[0].capture_group = photos[1].capture_group
    assert not dataset_coverage(photos, "evaluation")["evaluation_dataset_ready"]
    photos[0].capture_group = "restored"
    for p in photos:
        p.object_type = "painting"
    assert dataset_coverage(photos, "evaluation")["missing_object_types"] == [
        "building",
        "sculpture",
    ]


def corpus(tmp_path, object_id):
    photos = []
    for i, (split, expected, color) in enumerate(
        [
            ("calibration", object_id, "red"),
            ("calibration", None, "blue"),
            ("evaluation", object_id, "green"),
            ("evaluation", None, "yellow"),
        ]
    ):
        Image.new("RGB", (30, 30), color).save(tmp_path / f"{i}.png")
        photos.append(
            photo(
                id=str(i),
                path=f"{i}.png",
                split=split,
                expected_object_id=expected,
                category="full" if expected else "outside_reference",
                independent=True,
                capture_group=str(i),
            )
        )
    return photos


def test_fit_then_validate_binds_policy_photos_manifest_and_reference_sets(
    calibration_setup, tmp_path
):
    db, repository, encoder = calibration_setup
    asyncio.run(prepare_one(calibration_setup))
    object_id = str(db["objects"].documents[0]["_id"])
    photos = corpus(tmp_path, object_id)
    positives = {
        hashlib.sha256(load_photo(tmp_path, p.path)[0]).hexdigest()
        for p in photos
        if p.expected_object_id
    }
    encoder.describe = Mock(
        side_effect=lambda data, **kwargs: features(
            [1.0, 0.0] + [0.0] * 382
            if hashlib.sha256(data).hexdigest() in positives
            else [0.0, 1.0] + [0.0] * 382
        )
    )
    seed = BenchmarkPolicy(
        model=encoder.model,
        preprocessing=encoder.preprocessing,
        methods={
            method: Thresholds(minimum=0.45, automatic=0.99, margin=0.1)
            for method in ("global", "regional", "local")
        },
    )
    artifact = asyncio.run(calibrate(repository, encoder, photos, tmp_path, seed))
    assert (
        encoder.describe.call_count == 2
    )  # Held-out frames were not encoded for fitting.
    artifact.verify()
    report = asyncio.run(
        validate_calibration(repository, encoder, photos, tmp_path, artifact)
    )
    assert encoder.describe.call_count == 4
    assert report["methods"]["regional"]["automatic_correct"] == 1
    assert report["methods"]["regional"]["negative_false_automatic"] == 0
    assert not report["ready_for_regional_api_validation"]  # Too few photos.
    assert not report["phase5_complete"] and not report["automatic_api_enabled"]
    changed = deepcopy(artifact)
    changed.policy.methods["regional"].automatic = 0.1
    with pytest.raises(VisualFailure, match="CALIBRATION_ARTIFACT_CHANGED"):
        asyncio.run(
            validate_calibration(repository, encoder, photos, tmp_path, changed)
        )
    photos[0].provenance = "Changed annotation"
    with pytest.raises(VisualFailure, match="CALIBRATION_MANIFEST_CHANGED"):
        asyncio.run(
            validate_calibration(repository, encoder, photos, tmp_path, artifact)
        )
    photos[0].provenance = "synthetic fixture"
    db["visual_references"].documents[0]["set_id"] = "refreshed"
    with pytest.raises(VisualFailure, match="BENCHMARK_REFERENCE_SET_CHANGED"):
        asyncio.run(
            validate_calibration(repository, encoder, photos, tmp_path, artifact)
        )
    Image.new("RGB", (30, 30), "purple").save(tmp_path / photos[0].path)
    with pytest.raises(VisualFailure, match="BENCHMARK_PHOTO_CHANGED"):
        asyncio.run(
            validate_calibration(repository, encoder, photos, tmp_path, artifact)
        )


def test_missing_negative_photos_are_reported_before_database_or_model_work(tmp_path):
    photos = corpus(tmp_path, "123456789012345678901234")
    photos = [p for p in photos if p.expected_object_id]
    report = audit_dataset(photos, tmp_path)
    assert not report["ready_for_calibration"]
    manifest = tmp_path / "photos.json"
    manifest.write_text(json.dumps([p.model_dump() for p in photos]))
    args = SimpleNamespace(
        command="calibrate",
        manifest=str(manifest),
        output=str(tmp_path / "policy.json"),
    )
    with patch("app.modules.visual.__main__.MongoDB") as database:
        assert asyncio.run(run(args)) == 1
        database.assert_not_called()


def test_frozen_artifacts_are_not_overwritten(tmp_path):
    output = tmp_path / "policy.json"
    write_result({"frozen": True}, str(output))
    with pytest.raises(VisualFailure, match="VISUAL_OUTPUT_EXISTS"):
        write_result({"frozen": False}, str(output))
    assert '"frozen": true' in output.read_text()
