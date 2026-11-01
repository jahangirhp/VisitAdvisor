import pytest

from app.modules.live_camera.policy import (
    CameraCalibration,
    ProfileThresholds,
    pipeline_hash,
)
from app.modules.visual.features import digest
from scripts.camera_pipeline_eval import fit_profiles, summarize


def calibration():
    artifact = CameraCalibration(
        pipeline_hash=pipeline_hash(),
        model_sha256="a" * 64,
        reference_signature="b" * 64,
        object_regions=False,
        inference_budget_ms=1500,
        manifest_hash="c" * 64,
        calibration_photo_hashes={"photo": "d" * 64},
        normalized_photo_hashes={"photo": "e" * 64},
        calibration_capture_groups=["original-capture"],
        profiles={
            kind: ProfileThresholds(floor=0.8, margin=0.1, enabled=True)
            for kind in ("planar", "multiview")
        },
        fit_results={},
    )
    artifact.artifact_hash = digest(artifact.model_dump(exclude={"artifact_hash"}))
    return artifact


def test_calibration_binds_model_references_regions_budget_and_code():
    artifact = calibration()
    artifact.check_binding("b" * 64, "a" * 64, False, 1500)
    for args in [
        ("changed", "a" * 64, False, 1500),
        ("b" * 64, "changed", False, 1500),
        ("b" * 64, "a" * 64, True, 1500),
        ("b" * 64, "a" * 64, False, 1000),
    ]:
        with pytest.raises(ValueError, match="not current"):
            artifact.check_binding(*args)
    artifact.profiles["planar"].floor = 0.5
    with pytest.raises(ValueError, match="changed"):
        artifact.verify()


def test_changed_pipeline_code_invalidates_an_untampered_artifact(monkeypatch):
    artifact = calibration()
    monkeypatch.setattr(
        "app.modules.live_camera.policy.pipeline_hash", lambda: "changed-code"
    )
    with pytest.raises(ValueError, match="not current"):
        artifact.check_binding("b" * 64, "a" * 64, False, 1500)


def observation(index, expected="correct", score=0.9, kind="planar"):
    return {
        "http_status": 200,
        "expected_object_id": expected,
        "expected_profile": kind if expected else None,
        "independent": True,
        "budget_exceeded": False,
        "candidates": [
            {
                "id": "correct" if expected else "wrong",
                "score": score,
                "profile": kind,
                "coverage_ready": True,
            }
        ],
    }


def test_insufficient_samples_disable_profile_instead_of_claiming_precision():
    policy, result = fit_profiles([observation(0), observation(1, None)])
    assert not policy["planar"].enabled and not policy["multiview"].enabled
    assert result["planar"]["status"] == "insufficient_samples"


def test_false_high_scores_can_disable_a_profile_without_a_validation_error():
    rows = [
        observation(i, expected="correct" if i % 2 else "other", score=0.8)
        for i in range(10)
    ]
    for row in rows[:10]:
        row["candidates"][0]["id"] = row["expected_object_id"]
    rows += [observation(i + 10, None, score=0.95) for i in range(10)]
    policy, result = fit_profiles(rows)
    assert not policy["planar"].enabled
    assert result["planar"]["status"] == "disabled_no_safe_operating_point"


def test_safe_fit_uses_only_supplied_calibration_observations():
    rows = [
        observation(i, expected="correct" if i % 2 else "other", score=0.9)
        for i in range(10)
    ]
    for row in rows:
        row["candidates"][0]["id"] = row["expected_object_id"]
    rows += [observation(i + 10, None, score=0.7) for i in range(10)]
    policy, result = fit_profiles(rows)
    assert policy["planar"].enabled
    assert policy["planar"].floor > 0.7
    assert result["planar"]["incorrect_automatic_matches"] == 0
    assert not policy["multiview"].enabled


def test_failed_requests_remain_in_latency_and_accuracy_denominators():
    rows = [
        {
            "expected_object_id": "target",
            "suggestion": None,
            "http_status": 503,
            "top1_correct": False,
            "top3_correct": False,
            "seconds": 3.0,
            "warnings": [],
            "timings": {},
        }
    ]
    result = summarize(rows)
    assert result["top1"]["rate"] == 0 and result["top1"]["total"] == 1
    assert result["p95_request_seconds"] == 3
    assert result["suggestion_precision"]["rate"] is None
