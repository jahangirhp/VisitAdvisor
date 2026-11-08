"""Source-linked object derivatives for reference preparation, never source edits."""

import hashlib
import io
from math import ceil, floor
from typing import Any

from PIL import Image

from app.core.exceptions import ApplicationError
from app.modules.recognition.images import prepare_image

FOCUS_VERSION = "object-focus-v1"


def reference_image(
    photo: dict[str, Any], object_type: str
) -> tuple[bytes, dict[str, Any] | None]:
    source = prepare_image(bytes(photo["data"]), photo["content_type"])
    capture = photo.get("capture_metadata") or {}
    box = tuple(capture.get("target_box", (0, 0, 1, 1)))
    if (
        not capture
        or capture.get("profile") == "scene"
        or object_type.lower().strip() in {"room", "building"}
        or (box == (0, 0, 1, 1) and not photo.get("foreground_mask_data"))
    ):
        return source, None
    with Image.open(io.BytesIO(source)) as opened:
        rgb = opened.convert("RGB")
    mask_data = photo.get("foreground_mask_data")
    mask_hash = None
    if mask_data:
        mask_hash = hashlib.sha256(mask_data).hexdigest()
        if mask_hash != photo.get("foreground_mask", {}).get("sha256"):
            raise ApplicationError(
                "INVALID_REFERENCE_MASK", "Reference mask integrity check failed.", 409
            )
        with Image.open(io.BytesIO(mask_data)) as opened_mask:
            mask = opened_mask.convert("L")
        bounds = mask.getbbox()
        if mask.size != rgb.size or bounds is None:
            raise ApplicationError(
                "INVALID_REFERENCE_MASK",
                "Reference mask does not match its photo.",
                409,
            )
        focused = Image.new("RGB", rgb.size, "white")
        focused.paste(rgb, mask=mask)
        method = "masked-crop"
    else:
        bounds = (
            floor(box[0] * rgb.width),
            floor(box[1] * rgb.height),
            ceil(box[2] * rgb.width),
            ceil(box[3] * rgb.height),
        )
        focused = rgb
        method = "box-crop"
    selected_bounds = bounds
    left, top, right, bottom = bounds
    # Preserve a small border so the encoder's own center crop keeps the object.
    pad = max(1, round(max(right - left, bottom - top) * 0.08))
    side = max(right - left, bottom - top) + 2 * pad
    left = (left + right - side) // 2
    top = (top + bottom - side) // 2
    bounds = (left, top, left + side, top + side)
    # Copy only selected pixels onto the square white canvas. Padding must not
    # bring surrounding background back into manual-box references.
    cropped = Image.new("RGB", (side, side), "white")
    cropped.paste(
        focused.crop(selected_bounds),
        (selected_bounds[0] - left, selected_bounds[1] - top),
    )
    output = io.BytesIO()
    cropped.save(output, "PNG")
    data = output.getvalue()
    return data, {
        "version": FOCUS_VERSION,
        "method": method,
        "source_sha256": photo["sha256"],
        "normalized_source_sha256": hashlib.sha256(source).hexdigest(),
        "source_mask_sha256": mask_hash,
        "crop_pixels": list(bounds),
        "source_size": list(rgb.size),
        "image_sha256": hashlib.sha256(data).hexdigest(),
    }
