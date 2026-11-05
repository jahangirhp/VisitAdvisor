"""Controlled selection tests; boxes/masks and occlusion are supplied, not inferred."""

import io
from dataclasses import replace

import pytest
from PIL import Image, ImageFilter

pytest.importorskip("cv2")
pytest.importorskip("numpy")

from scripts.guided_capture_spike import Candidate, Shortlist, fixture, measure


def test_sharp_background_cannot_make_blurred_target_acceptable():
    import numpy as np

    sharp, box = fixture()
    blurred = sharp.filter(ImageFilter.GaussianBlur(4))
    background = np.random.default_rng(5).integers(
        16, 240, (512, 512, 3), dtype=np.uint8
    )
    image = Image.fromarray(background)
    image.paste(blurred.crop(box), box[:2])
    quality = measure(image, box)
    assert measure(image, (0, 0, 512, 512)).sharpness > quality.sharpness * 10
    assert (
        Shortlist(("front",)).consider(
            Candidate("blurred-target", "front", quality),
            target_confirmed=True,
            occluded=False,
        )
        == "hold_steady"
    )


def test_replaces_frame_only_when_target_quality_improves():
    image, box = fixture()
    sharp = measure(image, box)
    weaker = replace(sharp, sharpness=sharp.sharpness / 2)
    selected = Shortlist(("front",))
    assert (
        selected.consider(
            Candidate("weaker", "front", weaker), target_confirmed=True, occluded=False
        )
        == "selected"
    )
    assert (
        selected.consider(
            Candidate("sharp", "front", sharp), target_confirmed=True, occluded=False
        )
        == "improved"
    )
    assert (
        selected.consider(
            Candidate("weak-again", "front", weaker),
            target_confirmed=True,
            occluded=False,
        )
        == "keep_previous"
    )
    assert selected.selected["front"].frame_id == "sharp"


def test_relabelled_duplicate_does_not_establish_another_view():
    image, box = fixture()
    selected = Shortlist(("front", "left"))
    quality = measure(image, box)
    selected.consider(
        Candidate("first", "front", quality), target_confirmed=True, occluded=False
    )
    assert (
        selected.consider(
            Candidate("same-image", "left", quality),
            target_confirmed=True,
            occluded=False,
        )
        == "duplicate_view"
    )
    assert len(selected.selected) == 1


def test_jpeg_recompression_does_not_create_another_view():
    image, box = fixture()
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=82)
    with Image.open(io.BytesIO(stream.getvalue())) as recompressed:
        second = measure(recompressed, box)
    selected = Shortlist(("front", "left"))
    selected.consider(
        Candidate("original", "front", measure(image, box)),
        target_confirmed=True,
        occluded=False,
    )
    assert (
        selected.consider(
            Candidate("recompressed", "left", second),
            target_confirmed=True,
            occluded=False,
        )
        == "duplicate_view"
    )


def test_background_changes_do_not_establish_target_novelty():
    image, box = fixture()
    changed = Image.new("RGB", image.size, "blue")
    changed.paste(image.crop(box), box[:2])
    selected = Shortlist(("front", "left"))
    selected.consider(
        Candidate("original", "front", measure(image, box)),
        target_confirmed=True,
        occluded=False,
    )
    assert (
        selected.consider(
            Candidate("different-background", "left", measure(changed, box)),
            target_confirmed=True,
            occluded=False,
        )
        == "duplicate_view"
    )


@pytest.mark.parametrize(
    ("confirmed", "occluded", "expected"),
    [(False, False, "target_lost"), (True, True, "wait_for_clear_view")],
)
def test_lost_or_occluded_targets_never_enter_shortlist(confirmed, occluded, expected):
    image, box = fixture()
    selected = Shortlist(("front",))
    assert (
        selected.consider(
            Candidate("frame", "front", measure(image, box)),
            target_confirmed=confirmed,
            occluded=occluded,
        )
        == expected
    )
    assert selected.selected == {}


def test_tiny_or_clipped_target_requires_recapture():
    image, box = fixture()
    base = measure(image, box)
    selected = Shortlist(("front",))
    for changed, expected in (
        (replace(base, target_fraction=0.01), "move_closer"),
        (replace(base, clipped_fraction=0.8), "change_lighting"),
    ):
        assert (
            selected.consider(
                Candidate("frame", "front", changed),
                target_confirmed=True,
                occluded=False,
            )
            == expected
        )


def test_empty_mask_and_invalid_coordinates_fail_closed():
    image, box = fixture()
    with pytest.raises(ValueError, match="visible target"):
        measure(image, box, Image.new("L", image.size, 0))
    with pytest.raises(ValueError, match="coordinates"):
        measure(image, box, Image.new("L", (8, 8), 255))
    with pytest.raises(ValueError, match="target box"):
        measure(image, (-1, 0, 10, 10))


def test_mask_excludes_sharp_distractors_inside_the_target_box():
    import numpy as np

    image, box = fixture()
    target = image.filter(ImageFilter.GaussianBlur(4))
    rng = np.random.default_rng(13)
    noisy = Image.fromarray(rng.integers(16, 240, (512, 512, 3), dtype=np.uint8))
    center = (200, 200, 312, 312)
    noisy.paste(target.crop(center), center[:2])
    mask = Image.new("L", image.size, 0)
    mask.paste(255, center)
    assert measure(noisy, box).sharpness > measure(noisy, box, mask).sharpness * 10


def test_shortlist_is_bounded_and_unknown_steps_are_not_accepted():
    with pytest.raises(ValueError):
        Shortlist(("a", "b", "c", "d", "e"))
    image, box = fixture()
    selected = Shortlist(("front",))
    assert (
        selected.consider(
            Candidate("frame", "back", measure(image, box)),
            target_confirmed=True,
            occluded=False,
        )
        == "unknown_step"
    )
