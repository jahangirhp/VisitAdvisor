import asyncio
import json
from types import SimpleNamespace

import pytest

from scripts.artwork_room_replay import (
    ReplayManifest,
    audit_manifest,
    dataset_gates,
    historical_image_hashes,
    proportion,
    run,
    static_suggestion,
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


def acceptance_manifest():
    photos = []
    for index in range(50):
        positive = index < 30
        oid = f"{index // 6 + 1:024x}" if positive else None
        photos.append(
            {
                "id": str(index),
                "path": f"{index}.jpg",
                "sha256": f"{index + 1:064x}",
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
    return ReplayManifest.model_validate(
        {
            "purpose": "acceptance",
            "independence_reviewed": True,
            "camera_object_ids": [f"{i:024x}" for i in range(1, 6)],
            "photos": photos,
        }
    )


def test_regression_photos_and_repeated_sessions_cannot_pass_holdout_gate():
    manifest = acceptance_manifest()
    assert all(dataset_gates(manifest).values())
    manifest.purpose = "regression"
    assert not dataset_gates(manifest)["new_reviewed_holdout"]
    manifest.photos[0].capture_group = manifest.photos[1].capture_group
    assert not dataset_gates(manifest)["five_artworks_six_captures_each"]


def test_empty_proportions_do_not_claim_perfect_precision():
    assert proportion(0, 0)["rate"] is None
    assert proportion(0, 0)["wilson95"] is None
    assert proportion(30, 30)["wilson95"][0] < 0.98


def test_upload_does_not_inherit_camera_only_coverage_requirement():
    cases = passing_rows()
    for case in cases[10:30]:
        case["static_suggestion"] = None
    upload = summarize(cases, require_camera_coverage=False)
    assert all(upload["gates"].values())
    assert upload["correct_suggestion_coverage"]["correct"] == 10
    assert not summarize(cases)["gates"]["correct_suggestion_coverage_50_percent"]


def test_static_simulation_respects_server_abstention_and_disabled_profiles():
    candidates = [{"id": "artwork", "score": 0.95}]
    policy = {"candidate_floor": 0.55, "candidate_margin": 0.04}
    body = {"candidates": candidates, "suggestion_allowed": False}
    assert static_suggestion(body, candidates, policy, 200) is None
    body["suggestion_allowed"] = True
    assert static_suggestion(body, candidates, policy, 200) == "artwork"
    assert static_suggestion(body, candidates, policy, 503) is None
    body["candidates"] = [{**candidates[0], "coverage_ready": False}]
    assert static_suggestion(body, candidates, policy, 200) is None
    body["candidates"] = [{**candidates[0], "profile": "planar"}]
    policy["profiles"] = {"planar": {"enabled": False}}
    assert static_suggestion(body, candidates, policy, 200) is None
    policy["profiles"]["planar"] = {"enabled": True, "floor": 0.99, "margin": 0.02}
    assert static_suggestion(body, candidates, policy, 200) is None


def test_building_positives_cannot_satisfy_five_artwork_dataset():
    manifest = acceptance_manifest()
    manifest.photos[0].object_type = "building"
    assert not dataset_gates(manifest)["positive_targets_are_artworks"]


def test_history_excludes_input_manifest_but_keeps_previous_results(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    current = docs / "new-holdout.json"
    current.write_text(json.dumps({"photos": [{"sha256": "a" * 64}]}))
    (docs / "previous-result.json").write_text(
        json.dumps({"photo_hashes": {"prior": "b" * 64}})
    )
    assert historical_image_hashes(exclude_manifest=current) == {"b" * 64}
    assert historical_image_hashes() == {"a" * 64, "b" * 64}


def test_incomplete_acceptance_stops_before_settings_browser_or_models(
    tmp_path, monkeypatch
):
    manifest = acceptance_manifest()
    manifest.photos = manifest.photos[:2] + manifest.photos[30:]
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    monkeypatch.setattr(
        "scripts.artwork_room_replay.inspect_photos",
        lambda photos, root: (
            {p.id: p.sha256 for p in photos},
            {p.id: f"{int(p.id) + 1000:064x}" for p in photos},
        ),
    )
    monkeypatch.setattr(
        "scripts.artwork_room_replay.Settings",
        lambda: pytest.fail("Runtime configuration read before acceptance eligibility"),
    )
    _, audit = audit_manifest(path)
    assert not audit["ready_for_acceptance_run"]
    assert audit["recognition_requests_attempted"] == 0
    assert audit["positive_photos"] == 2
    assert audit["room_only_negatives"] == 20
    with pytest.raises(ValueError, match="ACCEPTANCE_DATASET_INCOMPLETE"):
        asyncio.run(run(path, "webkit", True, True))


def test_ticket_replay_pins_camera_mode_independently_of_environment(
    tmp_path, monkeypatch
):
    manifest = acceptance_manifest()
    manifest.purpose = "regression"
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    monkeypatch.setattr(
        "scripts.artwork_room_replay.inspect_photos",
        lambda photos, root: (
            {p.id: p.sha256 for p in photos},
            {p.id: f"{int(p.id) + 1000:064x}" for p in photos},
        ),
    )
    configured = []

    def copy_settings(*, update):
        values = {
            "live_camera_adaptive": True,
            "live_camera_object_regions": True,
            "live_camera_calibration": "unrelated-policy.json",
            **update,
        }
        configured.append(values)
        return SimpleNamespace(**values)

    monkeypatch.setattr(
        "scripts.artwork_room_replay.Settings",
        lambda: SimpleNamespace(model_copy=copy_settings),
    )

    def stop_before_browser(*args):
        raise RuntimeError("STOP_BEFORE_BROWSER")

    monkeypatch.setattr(
        "scripts.artwork_room_replay.browser_frames", stop_before_browser
    )
    with pytest.raises(RuntimeError, match="STOP_BEFORE_BROWSER"):
        asyncio.run(run(path, "webkit", True, True))
    assert configured[0]["live_camera_geometry_rerank"] is True
    assert configured[0]["live_camera_adaptive"] is False
    assert configured[0]["live_camera_object_regions"] is False
    assert configured[0]["live_camera_calibration"] is None
