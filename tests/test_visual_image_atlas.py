"""Opt-in positive-photo evaluation against Atlas, without camera caching.

RUN_VISUAL_IMAGE_CHECK=1 .venv/bin/pytest -s tests/test_visual_image_atlas.py
Set VISUAL_IMAGE_STRICT=1 as well to fail unless every photo ranks first.
The same datasets can be run from the UI at /api/v1/visual-evaluation.
"""

import asyncio
import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.visual.evaluation import (
    list_datasets,
    load_dataset,
    run_dataset,
    summarize,
)
from app.modules.visual.features import DinoEncoder

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_VISUAL_IMAGE_CHECK") != "1",
    reason="Explicit opt-in required for live Atlas image matching",
)

ROOT = Path(__file__).parent / "data" / "visual"


async def evaluate(name: str) -> list[dict]:
    settings = Settings()
    mongodb = MongoDB(settings)
    try:
        return await run_dataset(
            ROOT,
            load_dataset(ROOT, name),
            mongodb.database,
            DinoEncoder(),
            asyncio.Lock(),
        )
    finally:
        await mongodb.close()


@pytest.mark.parametrize("name", list_datasets(ROOT))
def test_atlas_photo_report(name):
    results = asyncio.run(evaluate(name))
    summary = summarize(results)
    for result in results:
        print(f"{result['id']} ({result['path']}): rank={result['rank']}")
        print(f"  {result['status']} {result['candidates']}")
    print(f"{name}: top-1 {summary['top1']}/{summary['count']}")
    print(f"{name}: top-3 {summary['top3']}/{summary['count']}")
    if os.environ.get("VISUAL_IMAGE_STRICT") == "1":
        assert summary["top1"] == summary["count"]
