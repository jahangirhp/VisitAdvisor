"""Read-only 3D reference coverage audit; no image inference or provider calls."""

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.live_camera.pipeline import profile
from app.modules.live_camera.references import AtlasCameraReferences


def summarize_object(obj: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    views = []
    for view in record["views"]:
        reference = view.get("reference", {})
        image_hash = view.get("image_hash", "")
        label = (reference.get("viewpoint") or "").strip().lower()
        views.append(
            {
                "image_sha256": image_hash,
                "valid_image_hash": bool(re.fullmatch(r"[a-f0-9]{64}", image_hash)),
                "viewpoint": label,
                "source_ids": reference.get("source_ids", []),
                "image_url": reference.get("image_url"),
                "rights_status": reference.get("rights_status", "unknown"),
            }
        )
    hashes = {v["image_sha256"] for v in views if v["valid_image_hash"]}
    labels = {v["viewpoint"] for v in views if v["viewpoint"]}
    labelled = [v for v in views if v["valid_image_hash"] and v["viewpoint"]]
    # Two labels attached to the same image do not establish multiple views.
    multiple_labelled_images = any(
        a["image_sha256"] != b["image_sha256"] and a["viewpoint"] != b["viewpoint"]
        for a in labelled
        for b in labelled
    )
    gaps = []
    if len(hashes) < 2:
        gaps.append("fewer_than_two_distinct_images")
    if not multiple_labelled_images:
        gaps.append("fewer_than_two_distinct_labelled_views")
    if any(not v["valid_image_hash"] for v in views):
        gaps.append("missing_or_invalid_image_hash")
    if any(not v["source_ids"] or not v["image_url"] for v in views):
        gaps.append("missing_source_link")
    if any(v["rights_status"] == "unknown" for v in views):
        gaps.append("rights_metadata_needs_review")
    return {
        "object_id": str(obj["_id"]),
        "name": obj["name"],
        "type": obj["type"],
        "canonical_object_uri": obj.get("external_id"),
        "set_id": record["set_id"],
        "reference_count": len(views),
        "distinct_images": len(hashes),
        "distinct_viewpoint_labels": len(labels),
        "structural_multiview_minimum_met": profile(obj["type"]) == "multiview"
        and multiple_labelled_images,
        "angle_coverage_verified": False,
        "gaps": gaps,
        "views": views,
    }


async def audit(cohort: dict[str, Any]) -> dict[str, Any]:
    settings = Settings().model_copy(
        update={
            "openai_api_key": None,
            "live_camera_location_id": cohort["location_id"],
            "live_camera_object_ids": cohort["object_ids"],
            "live_camera_adaptive": True,
            "live_camera_geometry_rerank": False,
            "live_camera_object_regions": False,
            "live_camera_calibration": None,
        }
    )
    db = MongoDB(settings)
    try:
        references = AtlasCameraReferences(
            db.database, settings, settings.live_camera_bundle
        )
        version, location, rows = await references.snapshot()
        objects = []
        for row in rows:
            obj = row["object"]
            if profile(obj["type"]) != "multiview":
                raise ValueError("3D cohort contains a planar object")
            record = await db.database["visual_references"].find_one(
                {
                    "location_id": cohort["location_id"],
                    "object_id": str(obj["_id"]),
                    "set_id": row["record"]["set_id"],
                },
                {
                    "set_id": 1,
                    "views.image_hash": 1,
                    "views.reference.viewpoint": 1,
                    "views.reference.source_ids": 1,
                    "views.reference.image_url": 1,
                    "views.reference.rights_status": 1,
                },
            )
            if record is None:
                raise ValueError("References changed")
            objects.append(summarize_object(obj, record))
        if (await references.snapshot())[0] != version:
            raise ValueError("References changed")
        return {
            "created_at": datetime.now(UTC).isoformat(),
            "purpose": "3d_reference_inventory",
            "location": location,
            "reference_version": version,
            "objects": objects,
            "structural_minimum_met_count": sum(
                o["structural_multiview_minimum_met"] for o in objects
            ),
            "recognition_requests_attempted": 0,
            "database_writes": 0,
            "accuracy_measured": False,
            "all_angle_coverage_claimed": False,
            "ready_for_release": False,
            "note": "Structural labels are not verified camera angles. Review "
            "identity, rights, capture groups and independent test photos before "
            "calibration. This inventory does not establish recognition accuracy.",
        }
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve previous evidence")
    try:
        report = asyncio.run(audit(json.loads(args.cohort.read_text())))
        with args.output.open("x") as stream:
            stream.write(json.dumps(report, indent=2) + "\n")
    except Exception:
        print("AUDIT_FAILED: check cohort, model metadata and Atlas readiness.")
        raise SystemExit(2) from None
    print(
        f"Audited {len(report['objects'])} objects; "
        f"{report['structural_minimum_met_count']} meet the structural minimum. "
        "No recognition accuracy measured."
    )


if __name__ == "__main__":
    main()
