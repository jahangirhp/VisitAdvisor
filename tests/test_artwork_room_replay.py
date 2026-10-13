from scripts.artwork_room_replay import (
    ReplayManifest,
    dataset_gates,
    proportion,
    summarize,
)


def row(index, expected="artwork", *, status=200, correct=True, suggestion=None):
    return {
        "photo_id": str(index),
        "expected_object_id": expected,
        "room_only": expected is None,
        "http_status": status,
        "top1_correct": correct and status == 200 and expected is not None,
        "top3_correct": correct and status == 200 and expected is not None,
        "static_suggestion": suggestion,
    }


def passing_rows():
    return [row(i, suggestion="artwork") for i in range(30)] + [
        row(i + 30, None, correct=False) for i in range(20)
    ]


def test_static_gate_requires_negative_samples_and_real_coverage():
    assert all(summarize(passing_rows())["gates"].values())
    positives_only = passing_rows()[:30]
    assert not summarize(positives_only)["gates"]["zero_room_only_suggestions"]
    abstentions_only = passing_rows()
    for case in abstentions_only:
        case["static_suggestion"] = None
    result = summarize(abstentions_only)
    assert result["suggestion_precision"]["rate"] is None
    assert not result["gates"]["correct_suggestion_coverage_50_percent"]


def test_service_failures_stay_in_accuracy_denominators_and_are_not_abstentions():
    cases = passing_rows()
    cases[0] = row(0, status=503, correct=False)
    result = summarize(cases)
    assert result["top1"]["total"] == 30
    assert result["top1"]["correct"] == 29
    assert result["service_failures"] == 1
    assert result["abstentions"] == 20
    assert not result["gates"]["all_requests_valid"]


def test_one_room_false_suggestion_fails_even_with_perfect_positive_rankings():
    cases = passing_rows()
    cases[-1]["static_suggestion"] = "artwork"
    result = summarize(cases)
    assert result["room_only_false_suggestions"] == 1
    assert not result["gates"]["zero_room_only_suggestions"]
    assert not result["gates"]["zero_wrong_static_suggestions"]


def test_top3_and_coverage_boundaries_are_enforced():
    cases = passing_rows()
    cases[0]["top3_correct"] = False
    assert summarize(cases)["gates"]["top3_95_percent"]
    cases[1]["top3_correct"] = False
    assert not summarize(cases)["gates"]["top3_95_percent"]
    cases = passing_rows()
    for case in cases[15:30]:
        case["static_suggestion"] = None
    assert summarize(cases)["gates"]["correct_suggestion_coverage_50_percent"]
    cases[14]["static_suggestion"] = None
    assert not summarize(cases)["gates"]["correct_suggestion_coverage_50_percent"]


def test_regression_photos_and_repeated_sessions_cannot_pass_holdout_gate():
    photos = []
    for index in range(50):
        positive = index < 30
        oid = f"{index // 6 + 1:024x}" if positive else None
        photos.append(
            {
                "id": str(index),
                "path": f"{index}.jpg",
                "sha256": "a" * 64,
                "location_id": "b" * 24,
                "expected_object_id": oid,
                "capture_group": f"capture-{index}",
                "split": "evaluation",
                "provenance": "Reviewed independent capture",
                "independent": True,
                "category": "full" if positive else "uninformative",
                "object_type": "painting" if positive else "building",
                "viewpoint": "Full view",
                "source_page": f"https://example.org/{index}",
                "license": "CC BY 4.0",
                "room_only": not positive,
            }
        )
    manifest = ReplayManifest.model_validate(
        {
            "purpose": "acceptance",
            "independence_reviewed": True,
            "camera_object_ids": [f"{i:024x}" for i in range(1, 6)],
            "photos": photos,
        }
    )
    assert all(dataset_gates(manifest).values())
    manifest.purpose = "regression"
    assert not dataset_gates(manifest)["new_reviewed_holdout"]
    manifest.photos[0].capture_group = manifest.photos[1].capture_group
    assert not dataset_gates(manifest)["five_artworks_six_captures_each"]


def test_empty_proportions_do_not_claim_perfect_precision():
    assert proportion(0, 0)["rate"] is None
    assert proportion(0, 0)["wilson95"] is None
    assert proportion(30, 30)["wilson95"][0] < 0.98
