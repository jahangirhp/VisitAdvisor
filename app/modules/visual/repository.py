"""Atomic reference-set publication and isolated Atlas image vectors."""

import asyncio
from typing import Any

from bson import BSON, ObjectId
from pymongo import timeout
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError
from pymongo.operations import ReplaceOne, SearchIndexModel, UpdateOne

from app.modules.live_camera.geometry import validate_local
from app.modules.visual.features import (
    DIMENSIONS,
    REGIONAL_GLOBAL_WEIGHT,
    VisualFailure,
    digest,
)

INDEX_NAME = "visitadvisor_visual_v1"
MAX_OBJECTS = 500
RUNTIME_FIELDS = (
    "location_id",
    "model",
    "preprocessing",
    "object_id",
    "evidence_hash",
    "set_id",
    "vector_count",
)


def evidence_hash(obj: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    return digest(
        {
            "object_id": str(obj["_id"]),
            "location_id": obj["location_id"],
            "external_id": obj.get("external_id"),
            "source_ids": sorted(obj.get("source_ids", [])),
            "sources": {
                str(s["_id"]): {"raw_record": s.get("raw_record"), "url": s.get("url")}
                for s in sources
            },
        }
    )


def evidence_snapshot(
    obj: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """Exact evidence copy for database-side freshness checks; never generated text."""
    return {
        "object_id": str(obj["_id"]),
        "location_id": obj["location_id"],
        "external_id": obj.get("external_id"),
        "source_ids": sorted(obj.get("source_ids", [])),
        "sources": [
            {
                "source_id": str(s["_id"]),
                "raw_record": s.get("raw_record"),
                "url": s.get("url"),
            }
            for s in sorted(sources, key=lambda s: str(s["_id"]))
        ],
    }


class VisualRepository:
    def __init__(self, db: AsyncDatabase[dict[str, Any]]) -> None:
        self.db = db
        self.references = db["visual_references"]
        self.vectors = db["visual_vectors"]

    async def ensure_indexes(self) -> None:
        await self.references.create_index(
            [("location_id", 1), ("object_id", 1)], unique=True
        )
        await self.vectors.create_index([("location_id", 1), ("set_id", 1)])
        # Upgrade early pilot sets without downloading images or repeating inference.
        # This CLI-only operation fills missing derived metadata atomically.
        with timeout(120):
            await self.references.update_many(
                {"vector_count": {"$exists": False}},
                [
                    {
                        "$set": {
                            "vector_count": {
                                "$sum": {
                                    "$map": {
                                        "input": {"$ifNull": ["$views", []]},
                                        "as": "view",
                                        "in": {
                                            "$size": {
                                                "$ifNull": [
                                                    "$$view.derived.regions",
                                                    [],
                                                ]
                                            }
                                        },
                                    }
                                }
                            }
                        }
                    }
                ],
            )
            await self.backfill_runtime_evidence()

    async def backfill_runtime_evidence(self) -> None:
        """Upgrade a bounded legacy pilot without changing set IDs or descriptors."""
        pending = (
            await self.references.find(
                {"canonical_evidence": {"$exists": False}},
                {"object_id": 1, "location_id": 1, "set_id": 1, "evidence_hash": 1},
            )
            .limit(MAX_OBJECTS + 1)
            .to_list()
        )
        if len(pending) > MAX_OBJECTS:
            raise VisualFailure("VISUAL_METADATA_UPGRADE_LIMIT")
        if not pending:
            return
        objects = (
            await self.db["objects"]
            .find(
                {"_id": {"$in": [ObjectId(r["object_id"]) for r in pending]}},
                {"location_id": 1, "external_id": 1, "source_ids": 1},
            )
            .to_list()
        )
        source_ids = {sid for obj in objects for sid in obj.get("source_ids", [])}
        sources = {
            str(s["_id"]): s
            for s in await self.db["sources"]
            .find(
                {"_id": {"$in": [ObjectId(sid) for sid in source_ids]}},
                {"location_id": 1, "raw_record": 1, "url": 1},
            )
            .to_list()
        }
        by_id = {str(obj["_id"]): obj for obj in objects}
        updates = []
        for record in pending:
            obj = by_id.get(record["object_id"])
            if obj is None or obj["location_id"] != record["location_id"]:
                continue
            ids = obj.get("source_ids", [])
            if not ids or any(
                sid not in sources or sources[sid]["location_id"] != obj["location_id"]
                for sid in ids
            ):
                continue
            evidence = [sources[sid] for sid in ids]
            if evidence_hash(obj, evidence) != record["evidence_hash"]:
                continue
            snapshot = {"canonical_evidence": evidence_snapshot(obj, evidence)}
            updates.append(
                UpdateOne(
                    {
                        "_id": record["_id"],
                        "set_id": record["set_id"],
                        "evidence_hash": record["evidence_hash"],
                        "canonical_evidence": {"$exists": False},
                        "$expr": {
                            "$lte": [
                                {
                                    "$add": [
                                        {"$bsonSize": "$$ROOT"},
                                        len(BSON.encode(snapshot)),
                                    ]
                                },
                                12 * 1024 * 1024,
                            ]
                        },
                    },
                    {"$set": snapshot},
                )
            )
        if updates:
            await self.references.bulk_write(updates)

    async def objects(
        self, location_id: str, *, summary: bool = False
    ) -> list[dict[str, Any]]:
        result = (
            await self.db["objects"]
            .find(
                {"location_id": location_id},
                {
                    "location_id": 1,
                    "external_id": 1,
                    "source_ids": 1,
                    "name": 1,
                    "type": 1,
                    "prepared_content.review_status": 1,
                }
                if summary
                else None,
            )
            .sort("_id", 1)
            .limit(MAX_OBJECTS + 1)
            .to_list()
        )
        if len(result) > MAX_OBJECTS:
            raise VisualFailure("LOCATION_EXCEEDS_VISUAL_PILOT_LIMIT")
        return result

    async def sources(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        ids = obj.get("source_ids", [])
        result = (
            await self.db["sources"]
            .find(
                {
                    "_id": {"$in": [ObjectId(value) for value in ids]},
                    "location_id": obj["location_id"],
                }
            )
            .to_list()
        )
        if not ids or len(result) != len(set(ids)):
            raise VisualFailure("REFERENCE_SOURCE_MISSING")
        return result

    async def current(self, location_id: str, object_id: str) -> dict[str, Any] | None:
        return await self.references.find_one(
            {"location_id": location_id, "object_id": object_id}
        )

    async def publish(
        self, record: dict[str, Any], previous: dict[str, Any] | None
    ) -> None:
        # Stage a complete version first. A failed write never replaces the active set.
        operations = []
        for view_index, view in enumerate(record["views"]):
            for region_index, region in enumerate(view["derived"]["regions"]):
                key = f"{record['set_id']}:{view_index}:{region_index}"
                operations.append(
                    ReplaceOne(
                        {"_id": key},
                        {
                            "_id": key,
                            "set_id": record["set_id"],
                            "object_id": record["object_id"],
                            "location_id": record["location_id"],
                            "model": record["model"],
                            "preprocessing": record["preprocessing"],
                            "dimensions": DIMENSIONS,
                            "embedding": region["embedding"],
                        },
                        upsert=True,
                    )
                )
        await self.vectors.bulk_write(operations)
        obj = await self.db["objects"].find_one(
            {"_id": ObjectId(record["object_id"]), "location_id": record["location_id"]}
        )
        sources = await self.sources(obj) if obj is not None else []
        if obj is None or evidence_hash(obj, sources) != record["evidence_hash"]:
            raise VisualFailure("REFERENCE_EVIDENCE_CHANGED")
        record["canonical_evidence"] = evidence_snapshot(obj, sources)
        if len(BSON.encode(record)) > 12 * 1024 * 1024:
            raise VisualFailure("REFERENCE_SET_TOO_LARGE")
        query = {"location_id": record["location_id"], "object_id": record["object_id"]}
        if previous is None:
            await self.references.insert_one(record)
        else:
            result = await self.references.replace_one(
                {**query, "set_id": previous["set_id"]}, record
            )
            if not result.matched_count:
                raise VisualFailure("REFERENCE_SET_CHANGED")
        # This previous set cannot become active again: publication uses a fresh
        # UUID and compare-and-swap. Cleanup failure does not undo publication.
        if previous is not None:
            try:
                await self.vectors.delete_many(
                    {"location_id": record["location_id"], "set_id": previous["set_id"]}
                )
            except PyMongoError:
                pass

    async def active(
        self,
        location_id: str,
        model: str,
        preprocessing: str,
        *,
        include_features: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        records, objects = await self.active_snapshot(
            location_id, model, preprocessing, include_features=include_features
        )
        return records, len(objects)

    async def active_snapshot(
        self,
        location_id: str,
        model: str,
        preprocessing: str,
        *,
        include_features: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Return fresh evidence-checked references and their current object summaries.

        This is a request-local read, not a cache or transactional snapshot. Runtime
        callers re-read after search to reject edits made during recognition.
        """
        if not include_features:
            return await self.runtime_snapshot(location_id, model, preprocessing)
        objects = {
            str(o["_id"]): o for o in await self.objects(location_id, summary=True)
        }
        source_ids = {
            sid for obj in objects.values() for sid in obj.get("source_ids", [])
        }
        # Offline SIFT benchmarks read substantially more than the API's projection.
        # Keep small cursor batches and bound the complete offline read to two minutes.
        with timeout(120):
            records, source_rows = await asyncio.gather(
                self.references.find(
                    {
                        "location_id": location_id,
                        "model": model,
                        "preprocessing": preprocessing,
                    },
                    {"views.reference.evidence": 0},
                )
                .batch_size(4)
                .limit(MAX_OBJECTS + 1)
                .to_list(),
                self.db["sources"]
                .find(
                    {
                        "location_id": location_id,
                        "_id": {"$in": [ObjectId(sid) for sid in source_ids]},
                    },
                    {"raw_record": 1, "url": 1},
                )
                .to_list(),
            )
        if len(records) > MAX_OBJECTS:
            raise VisualFailure("LOCATION_EXCEEDS_VISUAL_PILOT_LIMIT")
        sources = {str(s["_id"]): s for s in source_rows}
        active = []
        for record in records:
            obj = objects.get(record["object_id"])
            if obj is None:
                continue
            ids = obj.get("source_ids", [])
            if not ids or not set(ids).issubset(sources):
                continue
            current_hash = evidence_hash(obj, [sources[sid] for sid in ids])
            if record["evidence_hash"] == current_hash and record["views"]:
                active.append(record)
        return active, objects

    async def runtime_snapshot(
        self, location_id: str, model: str, preprocessing: str
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """Compare exact evidence inside MongoDB and return only small summaries."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"location_id": location_id}},
            {"$sort": {"_id": 1}},
            {"$limit": MAX_OBJECTS + 1},
            {
                "$project": {
                    "location_id": 1,
                    "external_id": 1,
                    "source_ids": 1,
                    "name": 1,
                    "type": 1,
                    "prepared_content.review_status": 1,
                    "_visual_object_id": {"$toString": "$_id"},
                    "_visual_source_ids": {
                        "$map": {
                            "input": {"$ifNull": ["$source_ids", []]},
                            "as": "source_id",
                            "in": {"$toObjectId": "$$source_id"},
                        }
                    },
                }
            },
            {
                "$lookup": {
                    "from": "sources",
                    "localField": "_visual_source_ids",
                    "foreignField": "_id",
                    "pipeline": [
                        {"$match": {"location_id": location_id}},
                        {"$sort": {"_id": 1}},
                        {"$project": {"raw_record": 1, "url": 1}},
                    ],
                    "as": "_visual_sources",
                }
            },
            {
                "$lookup": {
                    "from": "visual_references",
                    "localField": "_visual_object_id",
                    "foreignField": "object_id",
                    "let": {
                        "current_evidence": {
                            "object_id": "$_visual_object_id",
                            "location_id": "$location_id",
                            "external_id": {"$ifNull": ["$external_id", None]},
                            "source_ids": {
                                "$sortArray": {
                                    "input": {"$ifNull": ["$source_ids", []]},
                                    "sortBy": 1,
                                }
                            },
                            "sources": {
                                "$map": {
                                    "input": "$_visual_sources",
                                    "as": "source",
                                    "in": {
                                        "source_id": {"$toString": "$$source._id"},
                                        "raw_record": {
                                            "$ifNull": ["$$source.raw_record", None]
                                        },
                                        "url": {"$ifNull": ["$$source.url", None]},
                                    },
                                }
                            },
                        }
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "location_id": location_id,
                                "model": model,
                                "preprocessing": preprocessing,
                                "views.0": {"$exists": True},
                                "vector_count": {"$gt": 0},
                                "$expr": {
                                    "$eq": ["$canonical_evidence", "$$current_evidence"]
                                },
                            }
                        },
                        {"$project": {"_id": 0, **dict.fromkeys(RUNTIME_FIELDS, 1)}},
                    ],
                    "as": "_visual_references",
                }
            },
            {
                "$project": {
                    "_visual_sources": 0,
                    "_visual_source_ids": 0,
                    "_visual_object_id": 0,
                }
            },
        ]
        # The bounded pilot usually fits in one reply. MongoDB's default first
        # batch of 101 otherwise adds a round trip even for our 103-object corpus.

        rows = await (
            await self.db["objects"].aggregate(
                pipeline, batchSize=MAX_OBJECTS + 1, collation={"locale": "simple"}
            )
        ).to_list()
        if len(rows) > MAX_OBJECTS:
            raise VisualFailure("LOCATION_EXCEEDS_VISUAL_PILOT_LIMIT")
        objects = {}
        active = []
        for obj in rows:
            active.extend(obj.pop("_visual_references"))
            objects[str(obj["_id"])] = obj
        return active, objects

    async def index_status(self, create: bool = False) -> str:
        definition = {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": DIMENSIONS,
                    "similarity": "cosine",
                },
                *(
                    {"type": "filter", "path": key}
                    for key in (
                        "location_id",
                        "model",
                        "preprocessing",
                        "dimensions",
                        "set_id",
                    )
                ),
            ]
        }
        indexes = await (await self.vectors.list_search_indexes(INDEX_NAME)).to_list()
        if not indexes:
            if create:
                await self.vectors.create_search_index(
                    SearchIndexModel(
                        definition=definition, name=INDEX_NAME, type="vectorSearch"
                    )
                )
                return "building"
            return "missing"
        index = indexes[0]

        def fields(d: dict[str, Any]) -> list[dict[str, Any]]:
            return sorted(d.get("fields", []), key=lambda f: f["path"])

        if index.get("type") != "vectorSearch" or fields(
            index.get("latestDefinition", {})
        ) != fields(definition):
            raise VisualFailure("VISUAL_INDEX_CONFIGURATION_MISMATCH")
        return (
            "ready"
            if index.get("queryable") and index.get("status") == "READY"
            else "building"
        )

    async def verification_views(
        self,
        location_id: str,
        records: list[dict[str, Any]],
        object_ids: list[str],
        cache: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Read only the explicit, freshness-checked subset, never the full corpus."""
        selected = {r["object_id"]: r for r in records if r["object_id"] in object_ids}
        if not 1 <= len(object_ids) <= 12 or set(selected) != set(object_ids):
            if cache is not None:
                cache.clear()
            raise VisualFailure("VISUAL_VERIFICATION_REFERENCES_MISSING")
        storage = cache if cache is not None else {}
        expected = {oid: r["set_id"] for oid, r in selected.items()}
        for oid in list(storage):
            if oid not in expected or storage[oid]["set_id"] != expected[oid]:
                del storage[oid]
        missing = [oid for oid in object_ids if oid not in storage]
        try:
            # Cold descriptor loading is bounded separately from normal metadata
            # checks. No per-frame source records or complete corpus are fetched.
            with timeout(30):
                loaded = (
                    await self.references.find(
                        {
                            "location_id": location_id,
                            "set_id": {"$in": [expected[oid] for oid in missing]},
                            "object_id": {"$in": missing},
                        },
                        {"object_id": 1, "set_id": 1, "views.derived.local": 1},
                    )
                    .batch_size(1)
                    .limit(13)
                    .to_list()
                    if missing
                    else []
                )
        except Exception:
            storage.clear()
            raise VisualFailure("VISUAL_VERIFICATION_UNAVAILABLE") from None
        result = list(storage.values()) + loaded
        if len(result) != len(selected) or {
            r["object_id"]: r["set_id"] for r in result
        } != {oid: r["set_id"] for oid, r in selected.items()}:
            storage.clear()
            raise VisualFailure("VISUAL_REFERENCE_SET_CHANGED")
        try:
            for record in loaded:
                if not 1 <= len(record["views"]) <= 4:
                    raise ValueError("Invalid view count")
                for view in record["views"]:
                    local = view["derived"]["local"]
                    # Featureless references stay unverified rather than being
                    # invented or considered a successful geometric match.
                    if not local["points"] and not local["descriptors"]:
                        continue
                    validate_local(local)
        except Exception:
            storage.clear()
            raise VisualFailure("VISUAL_VERIFICATION_INVALID") from None
        storage.update({r["object_id"]: r for r in loaded})
        return result

    async def search(
        self,
        location_id: str,
        vector: list[float],
        records: list[dict[str, Any]],
        model: str,
        preprocessing: str,
    ) -> list[dict[str, Any]]:
        # Verify index coverage before returning rankings: Atlas indexes asynchronously.
        expected = {
            r["set_id"]: r["vector_count"]
            if "vector_count" in r
            else len(r["views"]) * 5
            for r in records
        }
        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": INDEX_NAME,
                    "path": "embedding",
                    "queryVector": vector,
                    "exact": True,
                    "limit": sum(expected.values()),
                    "filter": {
                        "location_id": location_id,
                        "model": model,
                        "preprocessing": preprocessing,
                        "dimensions": DIMENSIONS,
                        "set_id": {"$in": [r["set_id"] for r in records]},
                    },
                }
            },
            {
                "$project": {
                    "vector_id": "$_id",
                    "view_id": {"$arrayElemAt": [{"$split": ["$_id", ":"]}, 1]},
                    "object_id": 1,
                    "set_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
            {
                "$group": {
                    "_id": {"set_id": "$set_id", "view_id": "$view_id"},
                    "object_id": {"$first": "$object_id"},
                    "count": {"$sum": 1},
                    "regional_score": {"$max": "$score"},
                    "global_score": {
                        "$max": {
                            "$cond": [
                                {
                                    "$regexMatch": {
                                        "input": "$vector_id",
                                        "regex": ":0$",
                                    }
                                },
                                "$score",
                                -1,
                            ]
                        }
                    },
                }
            },
            {
                "$group": {
                    "_id": "$_id.set_id",
                    "object_id": {"$first": "$object_id"},
                    "count": {"$sum": "$count"},
                    "score": {
                        "$max": {
                            "$add": [
                                {
                                    "$multiply": [
                                        REGIONAL_GLOBAL_WEIGHT,
                                        "$global_score",
                                    ]
                                },
                                {
                                    "$multiply": [
                                        1 - REGIONAL_GLOBAL_WEIGHT,
                                        "$regional_score",
                                    ]
                                },
                            ]
                        }
                    },
                }
            },
        ]
        rows = await (await self.vectors.aggregate(pipeline)).to_list()
        if {row["_id"]: row["count"] for row in rows} != expected:
            raise VisualFailure("VISUAL_INDEX_UPDATING")
        return sorted(
            [
                {
                    "object_id": row["object_id"],
                    "score": 2 * row["score"] - 1,
                }
                for row in rows
            ],
            key=lambda row: (-row["score"], row["object_id"]),
        )[:3]
