import hashlib
import json
from io import BytesIO

import pytest
from PIL import Image

from scripts.visual_positive_test import load_inputs, summarize


def folder_manifest(tmp_path):
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "red").save(buffer, "JPEG")
    data = buffer.getvalue()
    (tmp_path / "photo.jpg").write_bytes(data)
    manifest = {
        "images": [
            {
                "file": "photo.jpg",
                "object_id": "a" * 24,
                "location_id": "b" * 24,
                "sha256": hashlib.sha256(data).hexdigest(),
                "capture_group": "visit-1",
                "source_page": "https://example.org/photo",
                "license": "CC BY-SA 4.0",
                "category": "cropped_partial",
            }
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_folder_rejects_unlabelled_images(tmp_path):
    folder_manifest(tmp_path)
    (tmp_path / "extra.jpg").write_bytes((tmp_path / "photo.jpg").read_bytes())
    with pytest.raises(ValueError, match="Every photo"):
        load_inputs(tmp_path)


def test_folder_rejects_changed_photo(tmp_path):
    folder_manifest(tmp_path)
    (tmp_path / "photo.jpg").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(tmp_path)


def test_folder_rejects_path_escape(tmp_path):
    manifest = folder_manifest(tmp_path)
    manifest["images"][0]["file"] = "../photo.jpg"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_inputs(tmp_path)


def test_folder_loads_frozen_labels(tmp_path):
    folder_manifest(tmp_path)
    photos, digest = load_inputs(tmp_path)
    assert photos[0].object_id == "a" * 24
    assert (
        digest == hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()
    )


def test_report_keeps_failures_in_accuracy_denominator():
    rows = [
        {
            "http_status": 200,
            "status": "ambiguous",
            "candidates": [{"object_id": "correct"}],
            "top1_correct": True,
            "top3_correct": True,
        },
        {
            "http_status": 503,
            "status": "error",
            "candidates": [],
            "top1_correct": False,
            "top3_correct": False,
        },
        {
            "http_status": 200,
            "status": "no_match",
            "candidates": [],
            "top1_correct": False,
            "top3_correct": False,
        },
    ]
    result = summarize(rows)
    assert result["top1_accuracy"] == 1 / 3
    assert result["top3_recall"] == 1 / 3
    assert result["service_failures"] == 1
    assert result["no_candidates"] == 1


def test_folder_rejects_multiple_crops_from_one_source(tmp_path):
    manifest = folder_manifest(tmp_path)
    first = manifest["images"][0]
    second = {**first, "file": "second.jpg"}
    manifest["images"].append(second)
    (tmp_path / "second.jpg").write_bytes((tmp_path / "photo.jpg").read_bytes())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="separate source photo"):
        load_inputs(tmp_path)
