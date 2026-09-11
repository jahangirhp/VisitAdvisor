"""Bounded image validation and metadata-free provider input."""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.exceptions import ApplicationError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 64 * 1024
MAX_PIXELS = 16_000_000
MIME_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def prepare_image(data: bytes, content_type: str | None) -> bytes:
    if content_type not in MIME_FORMATS:
        raise ApplicationError(
            "UNSUPPORTED_IMAGE", "Use a JPEG, PNG, or WebP image.", 415
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise ApplicationError("IMAGE_TOO_LARGE", "Image exceeds 5 MiB.", 413)
    if not data:
        raise ApplicationError("INVALID_IMAGE", "Image is empty or invalid.", 422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format != MIME_FORMATS[content_type]:
                    raise ApplicationError(
                        "IMAGE_TYPE_MISMATCH",
                        "Image bytes do not match its MIME type.",
                        415,
                    )
                if source.width * source.height > MAX_PIXELS:
                    raise ApplicationError(
                        "IMAGE_TOO_LARGE", "Image exceeds 16 megapixels.", 413
                    )
                if getattr(source, "n_frames", 1) != 1:
                    raise ApplicationError(
                        "ANIMATED_IMAGE", "Upload a single still image.", 415
                    )
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail((2560, 2560))
                # Composite transparency on white, preserving dark label lettering.
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=90)
                return output.getvalue()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ApplicationError(
            "IMAGE_TOO_LARGE", "Image dimensions are too large.", 413
        ) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ApplicationError(
            "INVALID_IMAGE", "Image is damaged or invalid.", 422
        ) from None
