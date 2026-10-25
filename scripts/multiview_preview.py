"""Run the selected 3D camera/photo preview without changing normal app settings.

uvicorn scripts.multiview_preview:create_app --factory --port 8005 --no-access-log
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI

from app.core.config import Settings
from app.core.domain import validate_id
from app.modules.live_camera.server import create_camera_app


def create_app() -> FastAPI:
    cohort = json.loads(
        Path(os.getenv("MULTIVIEW_COHORT", "docs/3d-pilot-cohort.json")).read_text()
    )
    validate_id(cohort["location_id"])
    ids = cohort["object_ids"]
    if not 2 <= len(ids) <= 12 or len(set(ids)) != len(ids):
        raise ValueError("3D preview needs 2–12 distinct object IDs")
    for object_id in ids:
        validate_id(object_id)
    calibration = os.getenv("MULTIVIEW_CALIBRATION")
    settings = Settings().model_copy(
        update={
            "openai_api_key": None,
            "live_camera_location_id": cohort["location_id"],
            "live_camera_object_ids": ids,
            "live_camera_geometry_rerank": False,
            "live_camera_adaptive": True,
            "live_camera_object_regions": False,
            "live_camera_calibration": Path(calibration) if calibration else None,
        }
    )
    return create_camera_app(configured_settings=settings)
