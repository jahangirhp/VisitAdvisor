"""Local, versioned image descriptors. Images never leave this process."""

import hashlib
import importlib
import io
import json
import math
from importlib.metadata import version
from typing import Any, Protocol

from PIL import Image

MODEL = "facebook/dinov2-small"
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
DIMENSIONS = 384
REGIONAL_GLOBAL_WEIGHT = 0.75
REGIONS = [(0.0, 0.0, 1.0, 1.0)] + [
    (x, y, x + 0.65, y + 0.65) for y in (0.0, 0.35) for x in (0.0, 0.35)
]


class VisualFailure(Exception):
    """Only fixed safe error codes, never URLs or provider exceptions."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def unit_vector(value: list[float], dimensions: int = DIMENSIONS) -> list[float]:
    if len(value) != dimensions or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in value
    ):
        raise VisualFailure("INVALID_VISUAL_VECTOR")
    norm = math.sqrt(sum(v * v for v in value))
    if not norm or not math.isfinite(norm):
        raise VisualFailure("INVALID_VISUAL_VECTOR")
    return [float(v / norm) for v in value]


class ImageEncoder(Protocol):
    model: str
    preprocessing: str

    def describe(self, image: bytes, *, regions: bool) -> dict[str, Any]: ...


class DinoEncoder:
    model = MODEL + "@" + REVISION

    def __init__(self, *, download: bool = False) -> None:
        try:
            self.torch = importlib.import_module("torch")
            transformers = importlib.import_module("transformers")
            self.cv = importlib.import_module("cv2")
            self.np = importlib.import_module("numpy")
            self.preprocessing = (
                "rgb-thumbnail2560-dino224-regions65-sift800-v1/"
                + "/".join(
                    version(p)
                    for p in (
                        "transformers",
                        "torch",
                        "opencv-python-headless",
                        "pillow",
                    )
                )
            )
            self.torch.set_num_threads(2)
            self.processor = transformers.AutoImageProcessor.from_pretrained(
                MODEL,
                revision=REVISION,
                local_files_only=not download,
                trust_remote_code=False,
                use_fast=False,
            )
            self.network = transformers.AutoModel.from_pretrained(
                MODEL,
                revision=REVISION,
                local_files_only=not download,
                trust_remote_code=False,
                use_safetensors=True,
            ).eval()
        except ImportError:
            raise VisualFailure("VISUAL_DEPENDENCIES_MISSING") from None
        except Exception:
            raise VisualFailure("VISUAL_MODEL_UNAVAILABLE") from None

    def describe(self, image: bytes, *, regions: bool) -> dict[str, Any]:
        try:
            with Image.open(io.BytesIO(image)) as source:
                rgb = source.convert("RGB")
            boxes = REGIONS if regions else REGIONS[:1]
            crops = [
                rgb.crop(
                    (
                        round(box[0] * rgb.width),
                        round(box[1] * rgb.height),
                        round(box[2] * rgb.width),
                        round(box[3] * rgb.height),
                    )
                )
                for box in boxes
            ]
            with self.torch.inference_mode():
                inputs = self.processor(images=crops, return_tensors="pt")
                vectors = self.network(**inputs).last_hidden_state[:, 0, :].tolist()
            gray_image = rgb.copy()
            gray_image.thumbnail((800, 800))
            gray = self.cv.cvtColor(self.np.array(gray_image), self.cv.COLOR_RGB2GRAY)
            points, descriptors = self.cv.SIFT_create(nfeatures=800).detectAndCompute(
                gray, None
            )
            points = points[:800]
            if descriptors is not None:
                descriptors = descriptors[:800]
            return {
                "regions": [
                    {"box": list(box), "embedding": unit_vector(vector)}
                    for box, vector in zip(boxes, vectors, strict=True)
                ],
                "local": {
                    "points": [list(p.pt) for p in points],
                    "descriptors": descriptors.tolist()
                    if descriptors is not None
                    else [],
                    "size": list(gray_image.size),
                },
                "size": list(rgb.size),
            }
        except VisualFailure:
            raise
        except Exception:
            raise VisualFailure("VISUAL_ENCODING_FAILED") from None


def geometric_evidence(
    query: dict[str, Any], reference: dict[str, Any]
) -> dict[str, float]:
    """SIFT ratio test and planar RANSAC baseline; not a 3D identity proof."""
    empty = {"score": 0.0, "query_coverage": 0.0, "reference_coverage": 0.0}
    if min(len(query["descriptors"]), len(reference["descriptors"])) < 8:
        return empty
    cv = importlib.import_module("cv2")
    np = importlib.import_module("numpy")
    matches = cv.BFMatcher().knnMatch(
        np.asarray(query["descriptors"], dtype=np.float32),
        np.asarray(reference["descriptors"], dtype=np.float32),
        k=2,
    )
    good = [
        pair[0]
        for pair in matches
        if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance
    ]
    # Multiple query features may not count as repeated evidence for one point.
    distinct = {m.trainIdx: m for m in sorted(good, key=lambda m: -m.distance)}
    good = list(distinct.values())
    if len(good) < 8:
        return empty
    src = np.float32([query["points"][m.queryIdx] for m in good]).reshape(-1, 1, 2)
    dst = np.float32([reference["points"][m.trainIdx] for m in good]).reshape(-1, 1, 2)
    cv.setRNGSeed(0)
    matrix, mask = cv.findHomography(src, dst, cv.RANSAC, 4.0)
    if matrix is None or mask is None or not np.isfinite(matrix).all():
        return empty
    inliers = int(mask.sum())
    if inliers < 8:
        return empty
    selected = mask.ravel().astype(bool)

    def coverage(points: Any, size: list[int] | None) -> float:
        if size is None:
            return 0.0
        area = float(cv.contourArea(cv.convexHull(points[selected])))
        return min(1.0, max(0.0, area / (size[0] * size[1])))

    return {
        "score": float(inliers / len(good) * min(inliers / 20, 1.0)),
        "query_coverage": coverage(src, query.get("size")),
        "reference_coverage": coverage(dst, reference.get("size")),
    }


def geometric_score(query: dict[str, Any], reference: dict[str, Any]) -> float:
    """Keep the established geometry gate independent of spatial diagnostics."""
    return geometric_evidence(query, reference)["score"]


def verified_similarity(evidence: dict[str, float], object_type: str) -> float:
    """Experimental spatial support, not a calibrated identity probability."""
    if evidence["score"] < 0.4:
        return 0.0
    if object_type == "building":
        return evidence["score"] * math.sqrt(evidence["reference_coverage"])
    return evidence["score"]


def rerank_verified(
    query: dict[str, Any],
    ranked: list[dict[str, Any]],
    records: list[dict[str, Any]],
    objects: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Experimental bounded planar verification, never automatic identification.

    Artwork references can recover exact details even when room context dominates
    the embedding. For a building/room reference, geometry confined to a small
    patch supports that patch, not identification of the containing space.
    Unverified objects retain their original embedding scores and remain eligible.
    """
    candidates = {c["object_id"]: dict(c) for c in ranked}
    for record in records:
        object_id = record["object_id"]
        obj = objects[object_id]
        score = 0.0
        for view in record["views"]:
            evidence = geometric_evidence(query["local"], view["derived"]["local"])
            # This score is a geometric similarity, not a probability. Weight
            # scene evidence by its support across the reference, rather than
            # letting one painting embedded in a room identify the room.
            similarity = verified_similarity(evidence, obj["type"])
            score = max(score, similarity)
        if score >= 0.45:
            candidates[object_id] = {"object_id": object_id, "score": score}
        elif obj["type"] == "building" and object_id in candidates:
            # The explicit verified subset uses geometry for its room candidates.
            # The rest of the location catalog remains embedding-ranked.
            del candidates[object_id]
    return sorted(candidates.values(), key=lambda c: (-c["score"], c["object_id"]))[:3]


def rank(
    query: dict[str, Any],
    records: list[dict[str, Any]],
    method: str,
    *,
    regional_global_weight: float = 0.0,
) -> list[dict[str, Any]]:
    candidates = []
    q = query["regions"][0]["embedding"]
    for record in records:
        score = -1.0
        for view in record["views"]:
            features = view["derived"]
            if method == "local":
                similarity = geometric_score(query["local"], features["local"])
            else:
                global_similarity = sum(
                    a * b
                    for a, b in zip(q, features["regions"][0]["embedding"], strict=True)
                )
                regional_similarity = max(
                    sum(a * b for a, b in zip(q, r["embedding"], strict=True))
                    for r in features["regions"]
                )
                similarity = (
                    global_similarity
                    if method == "global"
                    else regional_global_weight * global_similarity
                    + (1 - regional_global_weight) * regional_similarity
                )
            score = max(score, similarity)
        if score >= 0:
            candidates.append(
                {"object_id": record["object_id"], "score": min(score, 1.0)}
            )
    return sorted(candidates, key=lambda c: (-c["score"], c["object_id"]))[:3]
