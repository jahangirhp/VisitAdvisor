"""Bounded capture observations; requested views are never measured viewpoints."""

import hashlib
import io
import warnings
from typing import Annotated, Literal, Self

from PIL import Image, ImageChops, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.exceptions import ApplicationError
from app.modules.recognition.images import MAX_PIXELS

MAX_CAPTURE_METADATA_BYTES = 32 * 1024
MAX_FOREGROUND_MASK_BYTES = 512 * 1024
Normalized = Annotated[float, Field(ge=0, le=1)]
CaptureId = Annotated[
    str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
]


class CaptureQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    sharpness: float = Field(ge=0, le=1_000_000_000)
    clipped_fraction: Normalized
    target_fraction: Normalized


class CaptureSegmentation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    model: str = Field(min_length=1, max_length=200)
    model_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class GuidedCaptureMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    version: Literal["guided-capture-v1"]
    session_id: CaptureId
    frame_id: CaptureId
    profile: Literal["planar", "multiview", "front-only", "scene"]
    requested_view: (
        Literal[
            "front",
            "left",
            "right",
            "back",
            "oblique-left",
            "oblique-right",
            "elevated",
            "approach",
        ]
        | None
    ) = None
    # Prompt progression and image novelty cannot verify an object's real angle.
    # Only explicit operator review of the separate angle field may set this true.
    viewpoint_verified: bool = False
    target_box: tuple[Normalized, Normalized, Normalized, Normalized]
    quality: CaptureQuality
    segmentation: CaptureSegmentation | None = None

    @model_validator(mode="after")
    def valid_target_box(self) -> Self:
        x1, y1, x2, y2 = self.target_box
        if x1 >= x2 or y1 >= y2:
            raise ValueError("Target box must have positive width and height.")
        return self


def parse_capture_metadata(value: str | None) -> GuidedCaptureMetadata | None:
    if value is None:
        return None
    try:
        if len(value.encode("utf-8")) > MAX_CAPTURE_METADATA_BYTES:
            raise ValueError("Capture metadata is too large.")
        return GuidedCaptureMetadata.model_validate_json(value)
    except (ValidationError, ValueError):
        raise ApplicationError(
            "INVALID_CAPTURE_METADATA",
            "Capture details are invalid; retry capture.",
            422,
        ) from None


def prepare_foreground_mask(
    data: bytes, content_type: str | None, normalized_source: bytes
) -> tuple[bytes, dict[str, str | int]]:
    """Validate a separate, visible-pixel mask without changing source evidence."""
    if content_type != "image/png":
        raise ApplicationError(
            "UNSUPPORTED_FOREGROUND_MASK", "Use a PNG foreground mask.", 415
        )
    if len(data) > MAX_FOREGROUND_MASK_BYTES:
        raise ApplicationError(
            "FOREGROUND_MASK_TOO_LARGE", "Foreground mask exceeds 512 KiB.", 413
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as mask:
                if mask.width * mask.height > MAX_PIXELS:
                    raise Image.DecompressionBombError
                if (
                    mask.format != "PNG"
                    or getattr(mask, "n_frames", 1) != 1
                    or mask.mode not in {"1", "L", "RGB", "RGBA"}
                ):
                    raise ValueError("Invalid mask format.")
                with Image.open(io.BytesIO(normalized_source)) as source:
                    if mask.size != source.size:
                        raise ValueError("Mask dimensions do not match source.")
                mask.load()
                red, green, blue = mask.convert("RGB").split()
                if (
                    ImageChops.difference(red, green).getbbox()
                    or ImageChops.difference(red, blue).getbbox()
                    or (
                        mask.mode == "RGBA"
                        and mask.getchannel("A").getextrema() != (255, 255)
                    )
                ):
                    raise ValueError("Mask must be opaque grayscale.")
                histogram = red.histogram()
                if not histogram[255] or any(histogram[1:255]):
                    raise ValueError("Mask must contain binary foreground pixels.")
                output = io.BytesIO()
                red.save(output, format="PNG")
                clean = output.getvalue()
                if len(clean) > MAX_FOREGROUND_MASK_BYTES:
                    raise ValueError("Normalized mask is too large.")
                return clean, {
                    "sha256": hashlib.sha256(clean).hexdigest(),
                    "width": mask.width,
                    "height": mask.height,
                    "format": "binary-png-v1",
                }
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ApplicationError(
            "FOREGROUND_MASK_TOO_LARGE", "Foreground mask exceeds 16 megapixels.", 413
        ) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ApplicationError(
            "INVALID_FOREGROUND_MASK", "Foreground mask is invalid; retry capture.", 422
        ) from None
