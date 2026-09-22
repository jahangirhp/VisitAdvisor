"""Bounded local features for verifying planar painting candidates on the server."""

import importlib
from typing import Any

from PIL import Image


def validate_local(value: dict[str, Any]) -> None:
    import numpy as np

    points = np.asarray(value["points"], dtype=np.float32)
    descriptors = np.asarray(value["descriptors"], dtype=np.float32)
    size = np.asarray(value["size"], dtype=np.float32)
    if (
        size.shape != (2,)
        or not np.isfinite(size).all()
        or np.any(size <= 0)
        or np.any(size > 800)
        or points.ndim != 2
        or points.shape[1] != 2
        or not 8 <= len(points) <= 800
        or descriptors.shape != (len(points), 128)
        or not np.isfinite(points).all()
        or not np.isfinite(descriptors).all()
        or np.any(points < 0)
        or np.any(points >= size)
    ):
        raise ValueError("Invalid camera local features")


def describe_local(image: Image.Image) -> dict[str, Any]:
    import numpy as np

    cv = importlib.import_module("cv2")
    cv.setNumThreads(1)
    gray = cv.cvtColor(np.asarray(image), cv.COLOR_RGB2GRAY)
    points, descriptors = cv.SIFT_create(nfeatures=800).detectAndCompute(gray, None)
    return {
        "points": [list(point.pt) for point in points[:800]],
        "descriptors": descriptors[:800].tolist() if descriptors is not None else [],
        "size": list(image.size),
    }
