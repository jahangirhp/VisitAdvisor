"""Frozen preview thresholds bound to exact references, model and pipeline code."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.live_camera.pipeline import PIPELINE_VERSION
from app.modules.visual.features import digest


def pipeline_hash() -> str:
    root = Path(__file__).parent
    return digest(
        {
            name: (root / name).read_text()
            for name in (
                "pipeline.py",
                "regions.py",
                "inference.py",
                "geometry.py",
                "static/capture.mjs",
                "../visual/features.py",
            )
        }
    )


class ProfileThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    floor: float = Field(ge=0.45, le=1)
    margin: float = Field(ge=0.02, le=1)
    enabled: bool


class CameraCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = "camera-preview-calibration-v1"
    pipeline: str = PIPELINE_VERSION
    pipeline_hash: str
    model_sha256: str
    reference_signature: str
    object_regions: bool
    inference_budget_ms: int = Field(ge=250, le=2000)
    manifest_hash: str
    calibration_photo_hashes: dict[str, str]
    normalized_photo_hashes: dict[str, str]
    calibration_capture_groups: list[str]
    profiles: dict[str, ProfileThresholds]
    fit_results: dict[str, Any]
    artifact_hash: str = ""

    def verify(self) -> None:
        if (
            self.version != "camera-preview-calibration-v1"
            or self.pipeline != PIPELINE_VERSION
        ):
            raise ValueError("Invalid camera calibration version")
        if set(self.profiles) != {"planar", "multiview"}:
            raise ValueError("Missing camera verification profiles")
        if self.artifact_hash != digest(self.model_dump(exclude={"artifact_hash"})):
            raise ValueError("Camera calibration changed")

    def check_binding(
        self, signature: str, model_sha256: str, regions: bool, budget: int
    ) -> None:
        self.verify()
        if (
            self.reference_signature != signature
            or self.model_sha256 != model_sha256
            or self.object_regions != regions
            or self.inference_budget_ms != budget
            or self.pipeline_hash != pipeline_hash()
        ):
            raise ValueError("Camera calibration is not current")
