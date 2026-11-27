"""Immutable offline venue packages for the native iOS recognizer.

A package is derived only from an already validated ``CameraRecognizer.catalog``
(the Atlas snapshot the server itself recognizes against). It never reads or
writes MongoDB and never changes vectors: it re-encodes the same float32 values
into contiguous little-endian matrices with an explicit row mapping.
"""

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np

SCHEMA_VERSION = 1
PACKAGE_FORMAT = "visitadvisor-venue-package"
MINIMUM_APP_VERSION = "0.1.0"
DIMENSIONS = 384
SPACE_ID = "dinov2-small-cls-l2"
VECTOR_PATH = "vectors/dinov2-small-cls.f32"
POINTS_PATH = "verification/sift-points.f32"
DESCRIPTORS_PATH = "verification/sift-descriptors.f32"
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
PLACEHOLDER = re.compile(r"EXAMPLE_|REPLACE_WITH|UNCONFIGURED")

# The live server's camera contract (pipeline.py / regions.py / features.py).
# Any change there must change this ID so old app builds reject new packages.
PIPELINE_CONTRACT_ID = "object-regions-types-v5/camera-preprocessing-v1"


class PackageError(Exception):
    """Fixed safe codes only; never paths, URLs or database details."""


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def pipeline_contract(model: dict[str, Any]) -> dict[str, Any]:
    """Executable description of the server camera path the phone must reproduce."""
    return {
        "id": PIPELINE_CONTRACT_ID,
        "models": [
            {
                "role": "visualEmbedding",
                "implementation": "huggingface-transformers",
                "architecture": "dinov2-small",
                "source_model": model["source_model"],
                "output": "last_hidden_state[:,0,:] (CLS), L2-normalized",
                "dimensions": model["dimensions"],
                # The ONNX export is the server artifact. The Core ML export must
                # declare the same source_model and pass its own parity check.
                "server_export_sha256": model["sha256"],
                "preprocessing_contract": "camera-preprocessing-v1",
            }
        ],
        "camera_preprocessing": {
            "id": "camera-preprocessing-v1",
            "frame": "center square of side min(width,height)*224/256",
            "frame_sizes": [224, 512],
            "color": "RGB",
            "model_input": "resize to 224x224 bicubic",
            "scale": "pixel / 255",
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
            "layout": "NCHW float32",
            "server_frame_transport": "JPEG quality 0.9 (phone path has no JPEG)",
        },
        "quality_gate": {"max_channel_stddev_below": 2.0, "decision": "unreadable"},
        "region_proposals": {
            "enabled_by_policy_key": "object_regions",
            "working_size": 224,
            "maximum": 2,
            "canny": [40, 120],
            "gaussian_kernel": 3,
            "lab_contrast_floor": 20.0,
            "lab_contrast_percentile": 55,
            "area_range": [0.08, 0.85],
            "minimum_side_px": 28,
            "minimum_fill": 0.25,
            "padding_fraction": 0.06,
            "iou_full_frame_reject": 0.9,
            "iou_duplicate_reject": 0.7,
            "crop_resize": "bicubic back to frame size",
        },
        "retrieval": {
            "space_id": SPACE_ID,
            "metric": "dot product of L2-normalized vectors (raw cosine)",
            "view_score": "0.75 * score(region 0) + 0.25 * max(score(all regions))",
            "object_score": "max over views",
            "aggregate_across_boxes": "max per distinct object",
            "top_k_objects": 3,
        },
        "planar_verification": {
            "applies_to_profile": "planar",
            "planar_types": ["painting", "drawing", "print", "photograph"],
            "detector": "SIFT nfeatures=800 on grayscale region",
            "matcher": "BFMatcher L2 knn k=2, ratio 0.7, one match per reference point",
            "minimum_matches": 8,
            "homography": "RANSAC reprojection 4.0 px, RNG seed 0",
            "minimum_inliers": 8,
            "score": "inliers / good * min(inliers / 20, 1)",
            "accept_at_or_above": 0.4,
            "effect": "replaces embedding score for planar objects (0 if rejected)",
        },
    }


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PackageError(code)


def _finite_unit_rows(matrix: Any) -> None:
    _require(bool(np.isfinite(matrix).all()), "NON_FINITE_VECTOR")
    norms = np.linalg.norm(matrix, axis=1)
    _require(bool(np.all(norms > 0)), "ZERO_NORM_VECTOR")
    _require(bool(np.all(np.abs(norms - 1) < 1e-3)), "VECTOR_NOT_NORMALIZED")


def _write_sqlite(
    path: Path, catalog: dict[str, Any], rows: list[tuple], local_rows: list[tuple]
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            CREATE TABLE package_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE exhibits (
                id TEXT PRIMARY KEY,
                location_id TEXT NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                creator TEXT,
                year TEXT,
                description TEXT,
                profile TEXT NOT NULL CHECK (profile IN ('planar', 'multiview')),
                coverage_ready INTEGER NOT NULL,
                distinct_images INTEGER NOT NULL
            );
            CREATE TABLE exhibit_sources (
                exhibit_id TEXT NOT NULL REFERENCES exhibits(id),
                position INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                PRIMARY KEY (exhibit_id, position)
            );
            CREATE TABLE views (
                exhibit_id TEXT NOT NULL REFERENCES exhibits(id),
                view_index INTEGER NOT NULL,
                image_hash TEXT,
                viewpoint TEXT,
                rights_status TEXT,
                image_url TEXT,
                PRIMARY KEY (exhibit_id, view_index)
            );
            CREATE TABLE vector_rows (
                space_id TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                exhibit_id TEXT NOT NULL,
                view_index INTEGER NOT NULL,
                region_index INTEGER NOT NULL,
                PRIMARY KEY (space_id, row_index),
                FOREIGN KEY (exhibit_id, view_index)
                    REFERENCES views(exhibit_id, view_index)
            );
            CREATE TABLE local_features (
                exhibit_id TEXT NOT NULL,
                view_index INTEGER NOT NULL,
                first_point INTEGER NOT NULL,
                point_count INTEGER NOT NULL,
                width REAL NOT NULL,
                height REAL NOT NULL,
                PRIMARY KEY (exhibit_id, view_index),
                FOREIGN KEY (exhibit_id, view_index)
                    REFERENCES views(exhibit_id, view_index)
            );
            """
        )
        connection.executemany(
            "INSERT INTO package_info VALUES (?, ?)",
            [
                ("reference_version", catalog["reference_version"]),
                ("location_id", catalog["location"]["id"]),
                ("location_name", catalog["location"]["name"]),
            ],
        )
        for obj in sorted(catalog["objects"], key=lambda o: o["id"]):
            coverage = obj.get("reference_view_coverage") or {}
            connection.execute(
                "INSERT INTO exhibits VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    obj["id"],
                    obj["location_id"],
                    obj["name"],
                    obj.get("type", "other"),
                    obj.get("creator"),
                    None if obj.get("year") is None else str(obj["year"]),
                    obj.get("description"),
                    obj["recognition_profile"],
                    int(bool(coverage.get("ready", False))),
                    int(coverage.get("distinct_images", len(obj["views"]))),
                ),
            )
            connection.executemany(
                "INSERT INTO exhibit_sources VALUES (?, ?, ?, ?, ?)",
                [
                    (obj["id"], i, s["id"], s["title"], s.get("url"))
                    for i, s in enumerate(obj["sources"])
                ],
            )
            connection.executemany(
                "INSERT INTO views VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        obj["id"],
                        i,
                        v.get("image_hash"),
                        v.get("viewpoint"),
                        v.get("rights_status"),
                        v.get("image_url"),
                    )
                    for i, v in enumerate(obj["views"])
                ],
            )
        connection.executemany("INSERT INTO vector_rows VALUES (?, ?, ?, ?, ?)", rows)
        connection.executemany(
            "INSERT INTO local_features VALUES (?, ?, ?, ?, ?, ?)", local_rows
        )
        connection.commit()
        _require(
            connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
            "SQLITE_INVALID",
        )
    finally:
        connection.close()
    # A self-contained file: no WAL/journal may hold committed data.
    for suffix in ("-wal", "-journal", "-shm"):
        _require(not Path(str(path) + suffix).exists(), "SQLITE_NOT_FINALIZED")


def build_package(catalog: dict[str, Any], target: Path) -> dict[str, Any]:
    """Write a complete package directory atomically and return its manifest."""
    model = catalog["model"]
    policy = catalog["policy"]
    _require(
        model["dimensions"] == DIMENSIONS
        and model["input_size"] == 224
        and catalog.get("automatic_identification") is False
        and policy.get("recognition_pipeline") == "object-regions-types-v5",
        "UNSUPPORTED_CATALOG",
    )
    objects = sorted(catalog["objects"], key=lambda o: o["id"])
    _require(len({o["id"] for o in objects}) == len(objects) > 0, "INVALID_EXHIBITS")
    vectors: list[Any] = []
    rows: list[tuple] = []
    points: list[Any] = []
    descriptors: list[Any] = []
    local_rows: list[tuple] = []
    for obj in objects:
        _require(
            obj.get("recognition_profile") in {"planar", "multiview"}, "PROFILE_MISSING"
        )
        for view_index, view in enumerate(obj["views"]):
            matrix = np.asarray(view["vectors"], dtype=np.float32)
            _require(
                matrix.ndim == 2
                and matrix.shape[1] == DIMENSIONS
                and 1 <= len(matrix) <= 5,
                "INVALID_VECTOR_SHAPE",
            )
            for region_index, vector in enumerate(matrix):
                rows.append(
                    (SPACE_ID, len(vectors), obj["id"], view_index, region_index)
                )
                vectors.append(vector)
            if obj["recognition_profile"] == "planar":
                local = view.get("local")
                _require(local is not None, "PLANAR_LOCAL_FEATURES_MISSING")
                p = np.asarray(local["points"], dtype=np.float32)
                d = np.asarray(local["descriptors"], dtype=np.float32)
                _require(
                    p.ndim == 2 and p.shape[1] == 2 and d.shape == (len(p), 128),
                    "INVALID_LOCAL_FEATURES",
                )
                first = sum(len(x) for x in points)
                local_rows.append(
                    (
                        obj["id"],
                        view_index,
                        first,
                        len(p),
                        float(local["size"][0]),
                        float(local["size"][1]),
                    )
                )
                points.append(p)
                descriptors.append(d)
    matrix = np.ascontiguousarray(np.stack(vectors).astype("<f4"))
    _finite_unit_rows(matrix)
    point_matrix = (
        np.ascontiguousarray(np.concatenate(points).astype("<f4"))
        if points
        else np.zeros((0, 2), "<f4")
    )
    descriptor_matrix = (
        np.ascontiguousarray(np.concatenate(descriptors).astype("<f4"))
        if descriptors
        else np.zeros((0, 128), "<f4")
    )
    _require(
        bool(np.isfinite(point_matrix).all() and np.isfinite(descriptor_matrix).all()),
        "NON_FINITE_LOCAL_FEATURES",
    )

    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=target.parent, prefix=".stage-"))
    try:
        (stage / "vectors").mkdir()
        (stage / "verification").mkdir()
        (stage / VECTOR_PATH).write_bytes(matrix.tobytes())
        (stage / POINTS_PATH).write_bytes(point_matrix.tobytes())
        (stage / DESCRIPTORS_PATH).write_bytes(descriptor_matrix.tobytes())
        _write_sqlite(stage / "catalog.sqlite", catalog, rows, local_rows)
        contract = pipeline_contract(model)
        (stage / "pipeline-contract.json").write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n"
        )
        decision = {
            "id": catalog["policy"].get("calibration_hash", "uncalibrated-preview-v1"),
            "calibrated": bool(policy.get("profiles")),
            "automatic_identification": False,
            "candidate_floor": policy["candidate_floor"],
            "candidate_margin": policy["candidate_margin"],
            "ambiguous_floor": 0.45,
            "stable_frames": policy["stable_frames"],
            "minimum_stable_ms": policy.get("minimum_stable_ms", 600),
            "object_regions": bool(policy.get("object_regions", False)),
            "inference_budget_ms": policy.get("inference_budget_ms", 1500),
            "profiles": policy.get("profiles", {}),
            "score_convention": "raw_cosine_or_planar_geometry",
        }
        (stage / "decision-policy.json").write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n"
        )
        files = []
        for relative in (
            "catalog.sqlite",
            VECTOR_PATH,
            POINTS_PATH,
            DESCRIPTORS_PATH,
            "pipeline-contract.json",
            "decision-policy.json",
        ):
            path = stage / relative
            files.append(
                {
                    "path": relative,
                    "sizeBytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
        manifest = {
            "format": PACKAGE_FORMAT,
            "schemaVersion": SCHEMA_VERSION,
            "venueID": catalog["location"]["id"],
            "venueName": catalog["location"]["name"],
            "packageVersion": catalog["reference_version"],
            "catalogRevision": catalog.get(
                "reference_signature", catalog["reference_version"]
            ),
            "createdAt": catalog["created_at"],
            "minimumAppVersion": MINIMUM_APP_VERSION,
            "pipelineContractID": PIPELINE_CONTRACT_ID,
            "models": [
                {
                    "role": "visualEmbedding",
                    "sourceModel": model["source_model"],
                    "serverExportSHA256": model["sha256"],
                }
            ],
            "exhibitCount": len(objects),
            "viewCount": sum(len(o["views"]) for o in objects),
            "embeddingSpaces": [
                {
                    "spaceID": SPACE_ID,
                    "modelRole": "visualEmbedding",
                    "dimension": DIMENSIONS,
                    "rowCount": int(matrix.shape[0]),
                    "dtype": "float32",
                    "byteOrder": "little",
                    "normalization": "l2",
                    "metric": "dot",
                    "scoreConvention": "raw_cosine",
                    "matrixPath": VECTOR_PATH,
                }
            ],
            "localFeatures": {
                "pointCount": int(point_matrix.shape[0]),
                "pointsPath": POINTS_PATH,
                "descriptorsPath": DESCRIPTORS_PATH,
                "descriptorDimension": 128,
                "dtype": "float32",
                "byteOrder": "little",
            },
            "decisionPolicyID": decision["id"],
            "automaticIdentificationEnabled": False,
            "files": files,
            "requiredFiles": [f["path"] for f in files],
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        validate_package(stage)
        if target.exists():
            # Immutable: an existing version must be byte-identical, never replaced.
            existing = json.loads((target / "manifest.json").read_text())
            _require(existing["files"] == manifest["files"], "VERSION_CONTENT_CONFLICT")
            shutil.rmtree(stage)
            return existing
        os.replace(stage, target)
        return manifest
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    _require(
        bool(relative)
        and not parts.is_absolute()
        and ".." not in parts.parts
        and "\\" not in relative,
        "UNSAFE_PATH",
    )
    path = (root / relative).resolve()
    _require(root.resolve() in path.parents, "UNSAFE_PATH")
    return path


def validate_package(root: Path) -> dict[str, Any]:
    """Client-equivalent validation; the Swift validator implements the same rules."""
    manifest = json.loads((root / "manifest.json").read_text())
    _require(
        manifest.get("format") == PACKAGE_FORMAT
        and manifest.get("schemaVersion") == SCHEMA_VERSION
        and manifest.get("pipelineContractID") == PIPELINE_CONTRACT_ID
        and manifest.get("automaticIdentificationEnabled") is False,
        "INCOMPATIBLE_PACKAGE",
    )
    _require(not PLACEHOLDER.search(json.dumps(manifest)), "PLACEHOLDER_VALUE")
    total = 0
    listed = set()
    for entry in manifest["files"]:
        path = _safe_path(root, entry["path"])
        _require(path.is_file(), "MISSING_FILE")
        size = path.stat().st_size
        _require(size == entry["sizeBytes"], "SIZE_MISMATCH")
        _require(sha256_file(path) == entry["sha256"], "CHECKSUM_MISMATCH")
        total += size
        listed.add(entry["path"])
    _require(total <= MAX_PACKAGE_BYTES, "PACKAGE_TOO_LARGE")
    _require(set(manifest["requiredFiles"]) <= listed, "REQUIRED_FILE_UNLISTED")
    (space,) = manifest["embeddingSpaces"]
    n, d = space["rowCount"], space["dimension"]
    _require(
        d == DIMENSIONS
        and space["dtype"] == "float32"
        and space["byteOrder"] == "little",
        "INVALID_SPACE",
    )
    _require(n > 0 and n * d * 4 < 2**40, "INVALID_SPACE")
    data = (_safe_path(root, space["matrixPath"])).read_bytes()
    _require(len(data) == n * d * 4, "MATRIX_SIZE_MISMATCH")
    matrix = np.frombuffer(data, dtype="<f4").reshape(n, d)
    _finite_unit_rows(matrix)
    connection = sqlite3.connect(f"file:{root / 'catalog.sqlite'}?mode=ro", uri=True)
    try:
        indices = [
            r[0]
            for r in connection.execute(
                "SELECT row_index FROM vector_rows WHERE space_id = ? "
                "ORDER BY row_index",
                (space["spaceID"],),
            )
        ]
        _require(indices == list(range(n)), "ROW_MAPPING_INCOMPLETE")
        orphans = connection.execute(
            "SELECT COUNT(*) FROM vector_rows r LEFT JOIN views v "
            "ON v.exhibit_id = r.exhibit_id AND v.view_index = r.view_index "
            "WHERE v.exhibit_id IS NULL"
        ).fetchone()[0]
        _require(orphans == 0, "ROW_MAPPING_UNRESOLVED")
        exhibits = connection.execute("SELECT COUNT(*) FROM exhibits").fetchone()[0]
        _require(exhibits == manifest["exhibitCount"], "EXHIBIT_COUNT_MISMATCH")
        unrepresented = connection.execute(
            "SELECT COUNT(*) FROM exhibits e WHERE NOT EXISTS "
            "(SELECT 1 FROM vector_rows r WHERE r.exhibit_id = e.id)"
        ).fetchone()[0]
        _require(unrepresented == 0, "EXHIBIT_WITHOUT_REFERENCES")
        planar_missing = connection.execute(
            "SELECT COUNT(*) FROM views v JOIN exhibits e ON e.id = v.exhibit_id "
            "LEFT JOIN local_features l ON l.exhibit_id = v.exhibit_id "
            "AND l.view_index = v.view_index "
            "WHERE e.profile = 'planar' AND l.exhibit_id IS NULL"
        ).fetchone()[0]
        _require(planar_missing == 0, "PLANAR_LOCAL_FEATURES_MISSING")
        local = manifest["localFeatures"]
        point_total = 0
        for first, count in connection.execute(
            "SELECT first_point, point_count FROM local_features ORDER BY first_point"
        ):
            _require(
                first == point_total and 0 <= count <= 800, "LOCAL_MAPPING_INVALID"
            )
            point_total += count
        _require(point_total == local["pointCount"], "LOCAL_MAPPING_INVALID")
    finally:
        connection.close()
    points = _safe_path(root, local["pointsPath"]).stat().st_size
    descriptors = _safe_path(root, local["descriptorsPath"]).stat().st_size
    _require(
        points == point_total * 2 * 4 and descriptors == point_total * 128 * 4,
        "LOCAL_SIZE_MISMATCH",
    )
    _require(math.isfinite(float(total)), "PACKAGE_TOO_LARGE")
    return manifest
