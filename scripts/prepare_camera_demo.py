"""Build a two-painting demo from recorded v5 sources, without Atlas access.

This is explicitly a new derived demo snapshot, not the active Atlas reference set
or new evaluation evidence. Downloads are bounded and pixels are never saved.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.modules.live_camera.export import export_model
from app.modules.visual.features import DinoEncoder, digest, unit_vector
from app.modules.visual.references import ReferenceClient

DESTINATION = Path(".local/live-camera")
MANIFEST = Path("docs/server-camera-reference-manifest.json")
NAMES = {
    "6a9dd4126d52c3bc396586a0": "The Milkmaid",
    "6a9dd4136d52c3bc396586a1": "The Jewish Bride",
}


async def prepare() -> None:
    encoder = DinoEncoder()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    metadata = DESTINATION / "model-metadata.json"
    model_path = DESTINATION / "model.onnx"
    if not metadata.exists() or not model_path.exists():
        model = export_model(encoder, model_path)
        metadata.write_text(json.dumps(model, indent=2) + "\n")
    else:
        import hashlib

        model = json.loads(metadata.read_text())
        if (
            hashlib.sha256(model_path.read_bytes()).hexdigest() != model["sha256"]
            or model["source_model"] != encoder.model
            or model["reference_preprocessing"] != encoder.preprocessing
        ):
            raise RuntimeError("Model does not match current descriptors")
    manifest = json.loads(MANIFEST.read_text())
    objects = []
    async with httpx.AsyncClient() as http:
        for oid, name in NAMES.items():
            rows = [row for row in manifest if row["object_id"] == oid]
            views = []
            sources = []
            for row in rows:
                image, image_hash = await ReferenceClient(http).image(row["image_url"])
                features = await asyncio.to_thread(
                    encoder.describe, image, regions=True
                )
                views.append(
                    {
                        "vectors": [
                            unit_vector(r["embedding"]) for r in features["regions"]
                        ],
                        "local": features["local"],
                        "source_ids": row["source_ids"],
                        "image_url": row["image_url"],
                        "image_sha256": image_hash,
                        "viewpoint": row["viewpoint"],
                        "rights_status": row["rights_status"],
                        "evidence": row["evidence"],
                    }
                )
                sources.append(
                    {
                        "id": row["source_ids"][0],
                        "title": row["evidence"]["title"],
                        "url": row["evidence"]["source_page"],
                    }
                )
            objects.append(
                {
                    "id": oid,
                    "location_id": "6a9dd4106d52c3bc3965869d",
                    "name": name,
                    "type": "painting",
                    "creator": None,
                    "year": None,
                    "description": None,
                    "sources": sources,
                    "reference_set_id": "recorded-demo-" + digest(rows),
                    "evidence_hash": digest(rows),
                    "views": views,
                }
            )
    now = datetime.now(UTC)
    catalog = {
        "version": "live-camera-v1",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "location": {"id": "6a9dd4106d52c3bc3965869d", "name": "Rijksmuseum"},
        "origin": "recorded-camera-geometry-demo",
        "origin_manifest": str(MANIFEST),
        "origin_manifest_sha256": digest(manifest),
        "atlas_freshness_verified": False,
        "model": model,
        "objects": objects,
        "automatic_identification": False,
        "policy": {
            "candidate_floor": 0.55,
            "candidate_margin": 0.04,
            "stable_frames": 3,
            "global_weight": 0.75,
            "calibrated": False,
            "geometry_min_score": 0.4,
        },
    }
    staged = DESTINATION / "catalog.pending.json"
    staged.write_text(json.dumps(catalog, indent=2) + "\n")
    staged.replace(DESTINATION / "catalog.json")
    print("Prepared 2 demo objects from recorded sources; no Atlas reads or writes.")


if __name__ == "__main__":
    try:
        asyncio.run(prepare())
    except Exception:
        raise SystemExit(
            "Demo preparation failed; check model and reference access."
        ) from None
