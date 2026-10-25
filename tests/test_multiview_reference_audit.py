from scripts.multiview_reference_audit import summarize_object


def summarize(views, kind="sculpture"):
    return summarize_object(
        {"_id": "object", "name": "Sculpture", "type": kind},
        {"set_id": "immutable-set", "views": views},
    )


def view(image_hash="a" * 64, label="front"):
    return {
        "image_hash": image_hash,
        "reference": {
            "viewpoint": label,
            "source_ids": ["canonical-source"],
            "image_url": "https://example.org/sculpture.jpg",
            "rights_status": "recorded",
        },
    }


def test_relabelling_one_image_cannot_establish_multiview_coverage():
    result = summarize([view(), view(label="back")])
    assert result["distinct_images"] == 1
    assert not result["structural_multiview_minimum_met"]


def test_two_unlabelled_images_do_not_establish_multiview_coverage():
    result = summarize([view(label=""), view("b" * 64, "")])
    assert result["distinct_images"] == 2
    assert not result["structural_multiview_minimum_met"]


def test_labelled_distinct_images_are_not_proof_of_verified_camera_angles():
    result = summarize([view(), view("b" * 64, "back")])
    assert result["structural_multiview_minimum_met"]
    assert not result["angle_coverage_verified"]
    assert result["gaps"] == []


def test_malformed_hash_and_missing_provenance_are_reported():
    invalid = view("invalid", "back")
    invalid["reference"] = {"viewpoint": "back"}
    result = summarize([view(), invalid])
    assert not result["structural_multiview_minimum_met"]
    assert "missing_or_invalid_image_hash" in result["gaps"]
    assert "missing_source_link" in result["gaps"]
    assert "rights_metadata_needs_review" in result["gaps"]


def test_planar_object_cannot_pass_3d_inventory_gate():
    assert not summarize([view(), view("b" * 64, "back")], "painting")[
        "structural_multiview_minimum_met"
    ]
