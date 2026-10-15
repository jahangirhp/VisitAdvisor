"""Cheap bounded object-region proposals, not a semantic object detector."""

from typing import Any

from PIL import Image

FULL_FRAME = (0.0, 0.0, 1.0, 1.0)


def overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection) if intersection else 0.0


def proposals(image: Image.Image, maximum: int = 2) -> list[tuple[float, ...]]:
    import cv2
    import numpy as np

    if not 0 <= maximum <= 2 or image.size != (224, 224):
        raise ValueError("Invalid proposal bounds")
    if not maximum:
        return []
    cv2.setNumThreads(1)
    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    # Foreground contrast is useful for irregular silhouettes, including 3D
    # objects. It is only a proposal: no object identity/category is inferred.
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]))
    background = np.median(border, axis=0)
    contrast = np.linalg.norm(lab - background, axis=2)
    foreground = (contrast > max(20.0, float(np.percentile(contrast, 55)))).astype(
        np.uint8
    ) * 255
    foreground = cv2.morphologyEx(
        foreground, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)
    )
    candidates = []
    for mask in (edges, foreground):
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:100]:
            x, y, width, height = cv2.boundingRect(contour)
            area = width * height / (224 * 224)
            if not 0.08 <= area <= 0.85 or min(width, height) < 28:
                continue
            fill = cv2.contourArea(contour) / (width * height)
            if fill < 0.25:
                continue
            padding = max(3, round(min(width, height) * 0.06))
            box = (
                max(0, x - padding) / 224,
                max(0, y - padding) / 224,
                min(224, x + width + padding) / 224,
                min(224, y + height + padding) / 224,
            )
            distance = ((x + width / 2) / 224 - 0.5) ** 2 + (
                (y + height / 2) / 224 - 0.5
            ) ** 2
            candidates.append((fill * area / (1 + 4 * distance), box))
    selected: list[tuple[float, ...]] = []
    for _, box in sorted(candidates, reverse=True):
        if overlap(box, FULL_FRAME) > 0.9 or any(
            overlap(box, previous) > 0.7 for previous in selected
        ):
            continue
        selected.append(box)
        if len(selected) == maximum:
            break
    return selected


def crop(image: Image.Image, box: tuple[float, ...]) -> Image.Image:
    pixels: Any = tuple(round(value * 224) for value in box)
    return image.crop(pixels).resize((224, 224), Image.Resampling.BICUBIC)
