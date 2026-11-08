"""Reference derivatives remove background while retaining source provenance."""

import hashlib
import io

import pytest
from PIL import Image, ImageDraw

from app.core.exceptions import ApplicationError
from app.modules.admin.reference_focus import reference_image


def photo(background="blue", *, masked=True):
    image = Image.new("RGB", (120, 100), background)
    ImageDraw.Draw(image).rectangle((20, 30, 100, 70), fill="red")
    data = io.BytesIO()
    image.save(data, "PNG")
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rectangle((20, 30, 100, 70), fill=255)
    out = io.BytesIO()
    mask.save(out, "PNG")
    record = {
        "data": data.getvalue(),
        "sha256": hashlib.sha256(data.getvalue()).hexdigest(),
        "content_type": "image/png",
        "capture_metadata": {
            "profile": "multiview",
            "target_box": [1 / 6, 0.3, 101 / 120, 0.71],
        },
    }
    if masked:
        record.update(
            foreground_mask_data=out.getvalue(),
            foreground_mask={"sha256": hashlib.sha256(out.getvalue()).hexdigest()},
        )
    return record


def test_masked_crop_removes_background_and_preserves_whole_object():
    source = photo()
    original = source["data"]
    data, preparation = reference_image(source, "sculpture")
    image = Image.open(io.BytesIO(data))
    assert image.width == image.height
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((image.width // 2, image.height // 2))[0] > 240
    # All 81 source columns remain; a wide object must not be center-truncated.
    red_columns = [
        x
        for x in range(image.width)
        if image.getpixel((x, image.height // 2))[0] > 200
        and max(image.getpixel((x, image.height // 2))[1:]) < 80
    ]
    assert 79 <= max(red_columns) - min(red_columns) + 1 <= 81
    assert preparation["crop_pixels"][0] < 20
    assert preparation["crop_pixels"][2] > 100
    assert preparation["method"] == "masked-crop"
    assert preparation["source_sha256"] == source["sha256"]
    assert preparation["source_mask_sha256"] == source["foreground_mask"]["sha256"]
    assert hashlib.sha256(data).hexdigest() == preparation["image_sha256"]
    assert source["data"] == original
    other, _ = reference_image(photo("green"), "sculpture")
    # JPEG normalization can affect edge colors, but the focused border is white.
    assert Image.open(io.BytesIO(other)).getpixel((0, 0)) == (255, 255, 255)


def test_box_crop_is_explicit_and_scenes_and_unselected_frames_are_preserved():
    source = photo(masked=False)
    focused, preparation = reference_image(source, "artifact")
    assert preparation["method"] == "box-crop"
    for kind in ("room", "building"):
        data, preparation = reference_image(source, kind)
        assert preparation is None
        assert Image.open(io.BytesIO(data)).size == (120, 100)
    source["capture_metadata"]["target_box"] = [0, 0, 1, 1]
    data, preparation = reference_image(source, "artifact")
    assert preparation is None
    assert Image.open(io.BytesIO(data)).size == (120, 100)
    assert data != focused


def test_mask_integrity_failure_cannot_publish_a_derivative():
    source = photo()
    source["foreground_mask"]["sha256"] = "a" * 64
    with pytest.raises(ApplicationError, match="integrity"):
        reference_image(source, "sculpture")


def test_manual_box_padding_excludes_pixels_outside_the_selection():
    source = photo(masked=False)
    data, preparation = reference_image(source, "artifact")
    image = Image.open(io.BytesIO(data))
    assert preparation["method"] == "box-crop"
    assert image.width == image.height
    # The selected wide object leaves padding above/below on the square canvas.
    # That padding must be white, not the surrounding blue source background.
    assert image.getpixel((image.width // 2, 0)) == (255, 255, 255)
    assert image.getpixel((0, image.height // 2)) == (255, 255, 255)
    assert image.getpixel((image.width // 2, image.height // 2))[0] > 240


@pytest.mark.parametrize("box", [[0, 0, 1, 1], (0, 0, 1, 1)])
def test_full_frame_selection_is_not_treated_as_an_object_crop(box):
    source = photo(masked=False)
    source["capture_metadata"]["target_box"] = box
    data, preparation = reference_image(source, "artifact")
    assert preparation is None
    assert Image.open(io.BytesIO(data)).size == (120, 100)


def test_a_small_valid_box_retains_its_source_pixel():
    source = photo(masked=False)
    source["capture_metadata"]["target_box"] = [0.501, 0.501, 0.502, 0.502]
    data, preparation = reference_image(source, "artifact")
    image = Image.open(io.BytesIO(data))
    assert preparation["method"] == "box-crop"
    assert image.size == (3, 3)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((1, 1))[0] > 240
