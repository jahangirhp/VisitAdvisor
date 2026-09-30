"""Opt-in diagnostic image uploads against Atlas, without camera caching.

RUN_VISUAL_IMAGE_CHECK=1 .venv/bin/pytest -s tests/test_visual_image_atlas.py
Set VISUAL_IMAGE_STRICT=1 as well to fail on wrong rankings or false candidates.
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import Field, model_validator

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.main import create_app
from app.modules.visual.benchmark import Photo, load_photo, validate_manifest
from app.modules.visual.features import DinoEncoder


class AtlasPhoto(Photo):
    sha256: str = Field(pattern="^[a-f0-9]{64}$")
    in_database: bool

    @model_validator(mode="after")
    def check_expected_object(self):
        if self.in_database != (self.expected_object_id is not None):
            raise ValueError("in_database must agree with expected_object_id")
        return self


ROOT = Path(__file__).parent / "data" / "visual"
PHOTOS = [
    AtlasPhoto.model_validate(row)
    for row in json.loads((ROOT / "photos.json").read_text())
]
validate_manifest(PHOTOS)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_VISUAL_IMAGE_CHECK") != "1",
    reason="Explicit opt-in required for live Atlas image matching",
)


def recognize_with_atlas(image: bytes, location_id: str) -> dict:
    async def run() -> dict:
        settings = Settings()
        mongodb = MongoDB(settings)
        try:
            application = create_app()
            application.state.mongodb = mongodb
            application.state.settings = settings
            application.state.visual_encoder = DinoEncoder()
            application.state.visual_lock = asyncio.Lock()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/recognition/image",
                    data={"location_id": location_id, "mode": "visual"},
                    files={"image": ("photo.jpg", image, "image/jpeg")},
                )
            assert response.status_code == 200, response.json()
            return response.json()
        finally:
            await mongodb.close()

    return asyncio.run(run())


def test_atlas_photo_report():
    correct = 0
    positive_correct = 0
    positive_top3 = 0
    negative_correct = 0
    wrong = []
    positives = sum(photo.in_database for photo in PHOTOS)
    negatives = len(PHOTOS) - positives
    for photo in PHOTOS:
        image, raw_hash = load_photo(ROOT, photo.path)
        assert raw_hash == photo.sha256, f"Changed test image: {photo.id}"
        result = recognize_with_atlas(image, photo.location_id)
        assert result["method"] == "visual_embeddings", photo.id
        candidates = [
            (
                candidate["object_id"],
                candidate["name"],
                round(candidate["score"], 3),
            )
            for candidate in result["candidates"]
        ]
        print(f"{photo.id}: status={result['status']} candidates={candidates}")
        if photo.in_database:
            positive_top3 += photo.expected_object_id in [
                candidate_id for candidate_id, _, _ in candidates
            ]
            matched = bool(candidates and candidates[0][0] == photo.expected_object_id)
            positive_correct += matched
        else:
            matched = not candidates and result["status"] in (
                "no_match",
                "unreadable",
            )
            negative_correct += matched
        correct += matched
        if not matched:
            wrong.append(
                f"  {photo.id}: expected={photo.expected_object_id or 'no candidate'}, "
                f"status={result['status']}, candidates={candidates}"
            )

    def rate(count: int, total: int) -> str:
        return f"{count}/{total} ({count / total:.0%})" if total else "0/0 (n/a)"

    print(f"Real photos tested: {len(PHOTOS)}")
    print(f"Correct: {rate(correct, len(PHOTOS))}")
    print(f"Wrong: {rate(len(wrong), len(PHOTOS))}")
    print(f"Covered objects top-1: {rate(positive_correct, positives)}")
    print(f"Covered objects top-3: {rate(positive_top3, positives)}")
    print(f"Out-of-reference rejected: {rate(negative_correct, negatives)}")
    print("Wrong results:\n" + "\n".join(wrong) if wrong else "Wrong results: none")
    if os.environ.get("VISUAL_IMAGE_STRICT") == "1":
        assert not wrong, "Real-photo recognition results did not meet expectations"
