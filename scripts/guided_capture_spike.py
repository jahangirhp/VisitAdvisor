"""Offline feasibility probe; not a detector or production capture policy.

Target boxes, masks, identity continuity and occlusion flags are supplied inputs.
No model download, provider request, camera access or database write occurs.
Run with the existing camera-server extras: python -m scripts.guided_capture_spike.
"""

import argparse
import hashlib
import io
import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageFilter, ImageOps

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Quality:
    sharpness: float
    clipped_fraction: float
    target_fraction: float
    fingerprint: tuple[int, ...]

    @property
    def score(self) -> float:
        return math.log1p(self.sharpness) * (1 - self.clipped_fraction)


def measure(image: Image.Image, box: Box, mask: Image.Image | None = None) -> Quality:
    """Compare target interiors at a fixed working scale, ignoring background."""
    import cv2
    import numpy as np

    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
        raise ValueError("Invalid target box")
    if mask is not None and mask.size != image.size:
        raise ValueError("Mask must use original frame coordinates")
    target = ImageOps.contain(image.crop(box).convert("L"), (256, 256))
    if min(target.size) < 8:
        raise ValueError("Target is too small")
    gray = np.asarray(target, dtype=np.uint8)
    weights = (
        np.asarray(mask.crop(box).resize(target.size, Image.Resampling.NEAREST)) > 127
        if mask is not None
        else np.ones(gray.shape, dtype=bool)
    )
    fraction = (
        float(weights.mean()) * (x2 - x1) * (y2 - y1) / (image.width * image.height)
    )
    interior = cv2.erode(weights.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    interior[:2] = interior[-2:] = False
    interior[:, :2] = interior[:, -2:] = False
    if int(interior.sum()) < 32:
        raise ValueError("Insufficient visible target pixels")
    pixels = gray[interior]
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F)[interior].var())
    clipped = float(((pixels <= 8) | (pixels >= 247)).mean())
    # Fingerprint is a novelty heuristic, never proof of angle or identity.
    filled = np.where(weights, gray, float(pixels.mean())).astype(np.uint8)
    low = cv2.dct(cv2.resize(filled, (32, 32)).astype(np.float32))[:8, :8]
    coefficients = low.flatten()[1:]
    bits = tuple(int(v > np.median(coefficients)) for v in coefficients)
    return Quality(sharpness, clipped, fraction, bits)


@dataclass(frozen=True)
class Candidate:
    frame_id: str
    step: str
    quality: Quality


class Shortlist:
    """Bounded one-best-frame-per-prompt prototype; angles are not estimated."""

    def __init__(self, steps: tuple[str, ...]) -> None:
        if not 1 <= len(steps) <= 4 or len(set(steps)) != len(steps):
            raise ValueError("Use one to four distinct capture prompts")
        self.steps = steps
        self.selected: dict[str, Candidate] = {}

    def consider(
        self,
        candidate: Candidate,
        *,
        target_confirmed: bool,
        occluded: bool,
    ) -> str:
        if candidate.step not in self.steps:
            return "unknown_step"
        if not target_confirmed:
            return "target_lost"
        if occluded:
            return "wait_for_clear_view"
        quality = candidate.quality
        # Experimental thresholds for controlled fixtures, NOT calibrated gates.
        if quality.target_fraction < 0.08:
            return "move_closer"
        if quality.clipped_fraction > 0.35:
            return "change_lighting"
        if quality.sharpness < 40:
            return "hold_steady"
        previous = self.selected.get(candidate.step)
        for step, saved in self.selected.items():
            if (
                step != candidate.step
                and sum(
                    a != b
                    for a, b in zip(
                        quality.fingerprint, saved.quality.fingerprint, strict=True
                    )
                )
                <= 3
            ):
                return "duplicate_view"
        if previous is not None and quality.score <= previous.quality.score:
            return "keep_previous"
        self.selected[candidate.step] = candidate
        return "improved" if previous else "selected"


def fixture() -> tuple[Image.Image, Box]:
    import numpy as np

    rng = np.random.default_rng(42)
    texture = rng.integers(32, 224, (160, 160), dtype=np.uint8)
    image = Image.new("RGB", (512, 512), (120, 120, 120))
    image.paste(Image.fromarray(texture).convert("RGB"), (176, 176))
    return image, (176, 176, 336, 336)


def run_probe() -> dict[str, object]:
    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    sharp, box = fixture()
    blurred = sharp.filter(ImageFilter.GaussianBlur(4))
    rng = np.random.default_rng(7)
    distracted = Image.fromarray(rng.integers(16, 240, (512, 512, 3), dtype=np.uint8))
    distracted.paste(blurred.crop(box), box[:2])
    images = {"sharp": sharp, "blurred_target": blurred, "sharp_background": distracted}
    rows = {}
    for name, image in images.items():
        quality = measure(image, box)
        decision = Shortlist(("front",)).consider(
            Candidate(name, "front", quality), target_confirmed=True, occluded=False
        )
        full = measure(image, (0, 0, 512, 512))
        rows[name] = {
            "target_sharpness": quality.sharpness,
            "full_frame_sharpness": full.sharpness,
            "decision": decision,
        }
    encoded = io.BytesIO()
    sharp.save(encoded, "JPEG", quality=82)
    recompressed = measure(Image.open(io.BytesIO(encoded.getvalue())), box)
    original = measure(sharp, box)
    novelty = sum(
        a != b
        for a, b in zip(original.fingerprint, recompressed.fingerprint, strict=True)
    )
    timings = []
    for _ in range(50):
        start = perf_counter()
        measure(sharp, box)
        timings.append((perf_counter() - start) * 1000)
    result: dict[str, object] = {
        "scope": "synthetic target-box oracle; no detection/segmentation model tested",
        "fixtures": rows,
        "jpeg_fingerprint_distance": novelty,
        "quality_measurement_ms": {
            "p50": float(np.percentile(timings, 50)),
            "p95": float(np.percentile(timings, 95)),
            "samples": len(timings),
            "scope": "local host target-quality function only; no network/model",
        },
    }
    source = Path("tests/data/the-milkmaid.jpg")
    if source.exists():
        with Image.open(source) as image:
            image = ImageOps.contain(image.convert("RGB"), (512, 512))
            full_box = (0, 0, image.width, image.height)
            result["historical_photo_diagnostic"] = {
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "original_sharpness": measure(image, full_box).sharpness,
                "blurred_derivative_sharpness": measure(
                    image.filter(ImageFilter.GaussianBlur(4)), full_box
                ).sharpness,
                "scope": "existing single image, not independent accuracy evidence",
            }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    encoded = json.dumps(run_probe(), indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
