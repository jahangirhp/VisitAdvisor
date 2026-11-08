"""Bounded capture observations; requested views are never measured viewpoints."""

import hashlib
import io
import warnings
from typing import Annotated, Any, Literal, Self

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
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TargetBox = tuple[Normalized, Normalized, Normalized, Normalized]


class CaptureQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    sharpness: float = Field(ge=0, le=1_000_000_000)
    clipped_fraction: Normalized
    target_fraction: Normalized


class CaptureSegmentation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    model: str = Field(min_length=1, max_length=200)
    model_hash: Sha256 | None = None
    revision: str | None = Field(default=None, min_length=1, max_length=100)


class CaptureSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    method: Literal["server-mask", "server-suggestion", "manual-box", "full-frame"]
    reviewed: bool
    source_sha256: Sha256
    candidate_id: CaptureId | None = None
    mask_sha256: Sha256 | None = None


class CaptureAnalysis(CaptureSegmentation):
    """A model attempt is distinct from the operator's final selection."""

    source_sha256: Sha256
    prompt_point: tuple[Normalized, Normalized]
    prompt_box: TargetBox | None = None

    @model_validator(mode="after")
    def valid_prompt_box(self) -> Self:
        if self.prompt_box is not None:
            x1, y1, x2, y2 = self.prompt_box
            if x1 >= x2 or y1 >= y2:
                raise ValueError("Prompt box must have positive width and height.")
        return self


class GuidedCaptureMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    version: Literal["guided-capture-v1", "guided-capture-v2", "guided-capture-v3"]
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
    target_box: TargetBox
    quality: CaptureQuality
    segmentation: CaptureSegmentation | None = None
    selection: CaptureSelection | None = None
    analysis: CaptureAnalysis | None = None
    quality_region: Literal["full-frame"] | None = None
    detection_status: (
        Literal["pending", "suggested", "unavailable", "skipped"] | None
    ) = None

    @model_validator(mode="after")
    def valid_target_box(self) -> Self:
        x1, y1, x2, y2 = self.target_box
        if x1 >= x2 or y1 >= y2:
            raise ValueError("Target box must have positive width and height.")
        if self.version == "guided-capture-v1":
            if self.selection is not None or self.analysis is not None:
                raise ValueError("Reviewed selections require capture version 2.")
            if self.quality_region is not None or self.detection_status is not None:
                raise ValueError("Automatic observations require capture version 3.")
        elif self.selection is None:
            raise ValueError("Selection provenance is required.")
        else:
            if self.version == "guided-capture-v2":
                if not self.selection.reviewed:
                    raise ValueError("Version 2 requires explicit selection review.")
                if self.selection.method == "server-suggestion":
                    raise ValueError("Automatic suggestions require capture version 3.")
                if self.quality_region is not None or self.detection_status is not None:
                    raise ValueError(
                        "Automatic observations require capture version 3."
                    )
            else:
                if self.quality_region != "full-frame" or self.detection_status is None:
                    raise ValueError("Describe automatic observations explicitly.")
                if self.selection.method == "server-mask":
                    raise ValueError("Record a model result as a suggestion.")
                if (
                    self.selection.method == "manual-box"
                    and not self.selection.reviewed
                ):
                    raise ValueError("Manual corrections require an operator choice.")
                if self.selection.method == "server-suggestion" and (
                    self.detection_status != "suggested"
                    or self.segmentation is None
                    or self.segmentation.model_hash is None
                    or self.segmentation.revision is None
                ):
                    raise ValueError("A suggestion requires its pinned model identity.")
            if self.analysis and (
                self.analysis.source_sha256 != self.selection.source_sha256
            ):
                raise ValueError("Analysis and selection sources differ.")
            if self.selection.method in {"server-mask", "server-suggestion"}:
                if (
                    self.selection.candidate_id is None
                    or self.segmentation is None
                    or self.analysis is None
                    or self.segmentation.model != self.analysis.model
                    or self.segmentation.revision != self.analysis.revision
                    or self.segmentation.model_hash != self.analysis.model_hash
                ):
                    raise ValueError("A server mask requires its analysis provenance.")
            elif (
                self.segmentation is not None or self.selection.candidate_id is not None
            ):
                raise ValueError("Manual selections cannot claim a model mask.")
            if self.selection.method == "full-frame" and self.target_box != (
                0,
                0,
                1,
                1,
            ):
                raise ValueError("Full-frame selections must include the original.")
            if self.selection.method == "full-frame":
                if self.selection.mask_sha256 is not None:
                    raise ValueError("Full-frame selections do not have a mask.")
            elif self.selection.mask_sha256 is None:
                raise ValueError("Masked selections require the derivative hash.")
        return self


def validate_capture_source(
    metadata: dict[str, Any], source: bytes, mask: bytes | None
) -> None:
    """Verify derivative links before any photo or source records are written."""
    capture = metadata.get("capture_metadata") or {}
    if capture.get("version") not in {"guided-capture-v2", "guided-capture-v3"}:
        return
    selection = capture["selection"]
    valid = selection["source_sha256"] == hashlib.sha256(source).hexdigest()
    if selection["method"] != "full-frame" and mask is None:
        valid = False
    if selection["method"] == "full-frame" and mask is not None:
        valid = False
    if selection.get("mask_sha256"):
        valid = (
            valid
            and mask is not None
            and (selection["mask_sha256"] == hashlib.sha256(mask).hexdigest())
        )
    if not valid:
        raise ApplicationError(
            "INVALID_CAPTURE_METADATA",
            "Capture details do not match this photo; retry capture.",
            422,
        )


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
