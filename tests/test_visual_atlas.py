"""Opt-in real MongoDB join checks, isolated from the application's database.

RUN_VISUAL_ATLAS_CHECKS=1 .venv/bin/pytest tests/test_visual_atlas.py
Uses configured Atlas credentials and removes only its freshly named test database.
"""

import asyncio
import os
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.db.mongodb import MongoDB
from app.modules.visual.features import VisualFailure
from app.modules.visual.repository import (
    MAX_OBJECTS,
    VisualRepository,
    evidence_hash,
    evidence_snapshot,
)


@pytest.mark.skipif(
    os.environ.get("RUN_VISUAL_ATLAS_CHECKS") != "1",
    reason="Explicit opt-in required for temporary Atlas fixtures",
)
def test_runtime_join_isolation_projection_and_freshness():
    async def run():
        mongodb = MongoDB(Settings())
        # Keep below Atlas's 38-character database name limit.
        name = "va_visual_check_" + uuid4().hex[:16]
        db = mongodb.client[name]
        repo = VisualRepository(db)
        try:
            sources = [
                {
                    "_id": ObjectId(),
                    "location_id": location,
                    "url": "https://example.org/evidence",
                    "raw_record": {"title": "Canonical evidence", "nested": [1, 2]},
                }
                for location in ("location-one", "location-two")
            ]
            objects = [
                {
                    "_id": ObjectId(),
                    "location_id": location,
                    "source_ids": [str(source["_id"])],
                    "name": "Object",
                    "type": "sculpture",
                    "prepared_content": {
                        "review_status": "unreviewed",
                        "story": "Must not be returned in runtime summaries",
                    },
                }
                for location, source in (
                    ("location-one", sources[0]),
                    ("location-one", sources[1]),  # Invalid cross-location evidence.
                    ("location-two", sources[1]),
                )
            ]
            records = [
                {
                    "object_id": str(obj["_id"]),
                    "location_id": obj["location_id"],
                    "set_id": str(uuid4()),
                    "model": "fixture",
                    "preprocessing": "v1",
                    "evidence_hash": evidence_hash(obj, [source]),
                    "views": [
                        {"derived": {"regions": [{}]}, "reference": {"evidence": {}}}
                    ],
                }
                for obj, source in zip(
                    objects, [sources[0], sources[1], sources[1]], strict=True
                )
            ]
            await db["sources"].insert_many(sources)
            # Evidence must compare case-sensitively even if a catalog has a
            # case-insensitive default collation.
            await db.create_collection(
                "objects", collation={"locale": "en", "strength": 2}
            )
            await db["objects"].insert_many(objects)
            await db["visual_references"].insert_many(records)
            await repo.ensure_indexes()
            upgraded = await db["visual_references"].find().sort("_id", 1).to_list()
            await repo.ensure_indexes()
            assert (
                await db["visual_references"].find().sort("_id", 1).to_list()
                == upgraded
            )
            for i, (before, after) in enumerate(zip(records, upgraded, strict=True)):
                expected = {**before, "vector_count": 1}
                if i != 1:
                    expected["canonical_evidence"] = evidence_snapshot(
                        objects[i], [sources[0 if i == 0 else 1]]
                    )
                assert after == expected

            async def snapshot(location="location-one", model="fixture", prep="v1"):
                return await repo.runtime_snapshot(location, model, prep)

            active, summaries = await snapshot()
            assert [r["set_id"] for r in active] == [records[0]["set_id"]]
            assert len(summaries) == 2
            assert "views" not in active[0]
            assert active[0]["vector_count"] == 1
            assert summaries[str(objects[0]["_id"])]["prepared_content"] == {
                "review_status": "unreviewed"
            }
            assert len((await snapshot("location-two"))[0]) == 1
            assert not (await snapshot(model="other"))[0]
            assert not (await snapshot(prep="other"))[0]
            assert await snapshot("missing") == ([], {})
            reference_filter = {"set_id": records[0]["set_id"]}
            await db["visual_references"].update_one(
                reference_filter, {"$set": {"views": []}}
            )
            assert not (await snapshot())[0]
            await db["visual_references"].update_one(
                reference_filter, {"$set": {"views": records[0]["views"]}}
            )
            assert len((await snapshot())[0]) == 1

            source_filter = {"_id": sources[0]["_id"]}
            await db["sources"].update_one(
                source_filter, {"$set": {"raw_record.nested": [1, 3]}}
            )
            assert not (await snapshot())[0]
            await db["sources"].replace_one(source_filter, sources[0])
            assert len((await snapshot())[0]) == 1
            await db["sources"].update_one(
                source_filter, {"$set": {"raw_record.title": "CANONICAL EVIDENCE"}}
            )
            assert not (await snapshot())[0]
            await db["sources"].replace_one(source_filter, sources[0])
            await db["sources"].update_one(
                source_filter, {"$set": {"url": "https://example.org/changed"}}
            )
            assert not (await snapshot())[0]
            await db["sources"].replace_one(source_filter, sources[0])
            await db["sources"].delete_one(source_filter)
            assert not (await snapshot())[0]
            await db["sources"].insert_one(sources[0])
            await db["objects"].update_one(
                {"_id": objects[0]["_id"]}, {"$set": {"location_id": "location-two"}}
            )
            assert not (await snapshot())[0]
            await db["objects"].insert_many(
                {"location_id": "too-large", "source_ids": []}
                for _ in range(MAX_OBJECTS + 1)
            )
            with pytest.raises(
                VisualFailure, match="LOCATION_EXCEEDS_VISUAL_PILOT_LIMIT"
            ):
                await snapshot("too-large")
        finally:
            try:
                await mongodb.client.drop_database(name)
            finally:
                await mongodb.close()

    try:
        asyncio.run(run())
    except PyMongoError:
        pytest.fail(
            "Atlas fixture operation failed; check connectivity/access", pytrace=False
        )
