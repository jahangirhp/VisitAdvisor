"""Read-only synthetic integration benchmark; never a real-photo accuracy claim.

Run from the repository root after preparing references:
  python -m scripts.visual_smoke --location-id LOCATION_ID
"""

import argparse
import asyncio
import io
import json
import tempfile
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import MongoDB
from app.modules.visual.benchmark import (
    BenchmarkPolicy,
    Photo,
    Thresholds,
    benchmark,
)
from app.modules.visual.features import DinoEncoder, VisualFailure
from app.modules.visual.references import ReferenceClient
from app.modules.visual.repository import VisualRepository


async def run(location_id: str) -> None:
    database = MongoDB(Settings())
    try:
        repository = VisualRepository(database.database)
        encoder = await asyncio.to_thread(DinoEncoder)
        records, _ = await repository.active(
            location_id, encoder.model, encoder.preprocessing
        )
        if len(records) < 3:
            raise VisualFailure("SMOKE_REQUIRES_THREE_REFERENCED_OBJECTS")
        policy = BenchmarkPolicy(
            model=encoder.model,
            preprocessing=encoder.preprocessing,
            methods={
                method: Thresholds(
                    minimum=0.45 if method != "local" else 0.2,
                    automatic=0.95,
                    margin=0.1,
                )
                for method in ("global", "regional", "local")
            },
        )
        with tempfile.TemporaryDirectory(prefix="visitadvisor-visual-smoke-") as folder:
            root = Path(folder)
            photos = []
            async with httpx.AsyncClient() as http:
                for index, record in enumerate(
                    sorted(records, key=lambda r: r["object_id"])[:3]
                ):
                    data, _ = await ReferenceClient(http).image(
                        record["views"][0]["reference"]["image_url"]
                    )
                    with Image.open(io.BytesIO(data)) as original:
                        full = original.convert("RGB")
                    width, height = full.size
                    variants = [
                        ("full", full),
                        (
                            "partial",
                            full.crop(
                                (
                                    width // 4,
                                    height // 4,
                                    3 * width // 4,
                                    3 * height // 4,
                                )
                            ),
                        ),
                        (
                            "full",
                            full.transform(
                                full.size,
                                Image.Transform.PERSPECTIVE,
                                (1, 0.15, -0.05 * width, 0.05, 1, 0, 0.00012, 0.00008),
                                resample=Image.Resampling.BICUBIC,
                                fillcolor="white",
                            ),
                        ),
                    ]
                    for variant, (category, image) in enumerate(variants):
                        name = f"{index}-{variant}.jpg"
                        image.save(root / name)
                        photos.append(
                            Photo(
                                id=name,
                                path=name,
                                location_id=location_id,
                                expected_object_id=record["object_id"],
                                capture_group=f"reference-{index}",
                                split="evaluation",
                                provenance="Synthetic museum-reference derivative",
                                independent=False,
                                category=category,
                                object_type="painting",
                                viewpoint="Synthetic full/crop/perspective check",
                            )
                        )
                for index in range(3):
                    image = Image.new("RGB", (300, 200), (80 * index, 100, 120))
                    ImageDraw.Draw(image).ellipse((30, 30, 200, 170), fill="white")
                    name = f"negative-{index}.jpg"
                    image.save(root / name)
                    photos.append(
                        Photo(
                            id=name,
                            path=name,
                            location_id=location_id,
                            expected_object_id=None,
                            capture_group=name,
                            split="evaluation",
                            provenance="Procedural uninformative shape",
                            independent=False,
                            category="uninformative",
                            object_type="synthetic",
                            viewpoint="none",
                        )
                    )
            report = await benchmark(
                repository, encoder, photos, root, policy, "evaluation"
            )
        print(json.dumps(report, indent=2))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location-id", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.location_id))
    except (
        VisualFailure,
        ApplicationError,
        ValidationError,
        PyMongoError,
        OSError,
    ) as exc:
        code = str(exc) if isinstance(exc, VisualFailure) else "SMOKE_CHECK_FAILED"
        print(json.dumps({"error": {"code": code}}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
