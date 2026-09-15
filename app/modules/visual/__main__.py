"""Prepare references, manage the visual index, and compare visual baselines."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.core.exceptions import ApplicationError
from app.db.mongodb import MongoDB
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService
from app.modules.visual.benchmark import BenchmarkPolicy, Photo, benchmark
from app.modules.visual.calibration import (
    Calibration,
    audit_dataset,
    calibrate,
    validate_calibration,
)
from app.modules.visual.features import DinoEncoder, VisualFailure
from app.modules.visual.preparation import ManualReference, prepare
from app.modules.visual.repository import VisualRepository


async def run(args: argparse.Namespace) -> int:
    database = None
    try:
        if args.command in ("audit", "calibrate", "validate"):
            if args.output and Path(args.output).exists():
                raise VisualFailure("VISUAL_OUTPUT_EXISTS")
            photos = [Photo.model_validate(row) for row in read_json(args.manifest)]
            root = Path(args.manifest).resolve().parent
            audit = audit_dataset(photos, root)
            if args.command == "audit":
                write_result(audit, args.output)
                return 0
            if args.command == "calibrate" and not audit["ready_for_calibration"]:
                print(
                    json.dumps(
                        {
                            "error": {"code": "CALIBRATION_DATASET_INCOMPLETE"},
                            "audit": audit,
                        },
                        indent=2,
                    )
                )
                return 1
        if args.command == "model":
            encoder = await asyncio.to_thread(DinoEncoder, download=True)
            print(
                json.dumps(
                    {"model": encoder.model, "preprocessing": encoder.preprocessing}
                )
            )
            return 0
        settings = Settings()
        database = MongoDB(settings)
        await database.ping()
        repository = VisualRepository(database.database)
        if args.command == "index":
            await repository.ensure_indexes()
            print(json.dumps({"status": await repository.index_status(args.create)}))
            return 0
        if args.command == "prepare":
            location = await LocationService(LocationRepository(database.database)).get(
                args.location_id
            )
            encoder = await asyncio.to_thread(DinoEncoder)
            manual = None
            if args.manifest:
                manual = [
                    ManualReference.model_validate(row)
                    for row in read_json(args.manifest)
                ]
            await repository.ensure_indexes()
            async with httpx.AsyncClient() as http:
                report = await prepare(
                    repository,
                    location.id,
                    encoder,
                    http,
                    args.limit,
                    args.offset,
                    manual,
                )
            print(json.dumps(report, indent=2))
            return 1 if report["failed"] else 0
        encoder = await asyncio.to_thread(DinoEncoder)
        if args.command == "calibrate":
            artifact = await calibrate(
                repository,
                encoder,
                photos,
                root,
                BenchmarkPolicy.model_validate(read_json(args.policy)),
            )
            write_result(artifact.model_dump(), args.output)
            return 0
        if args.command == "validate":
            report = await validate_calibration(
                repository,
                encoder,
                photos,
                root,
                Calibration.model_validate(read_json(args.calibration)),
            )
            write_result(report, args.output)
            return 0 if report["ready_for_regional_api_validation"] else 1
        photos = [Photo.model_validate(row) for row in read_json(args.manifest)]
        policy = BenchmarkPolicy.model_validate(read_json(args.policy))
        report = await benchmark(
            repository,
            encoder,
            photos,
            Path(args.manifest).resolve().parent,
            policy,
            args.split,
        )
        print(json.dumps(report, indent=2))
        return 0
    except (
        VisualFailure,
        ApplicationError,
        ValidationError,
        PyMongoError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        code = (
            str(exc)
            if isinstance(exc, VisualFailure)
            else exc.code
            if isinstance(exc, ApplicationError)
            else "VISUAL_OPERATION_FAILED"
        )
        print(json.dumps({"error": {"code": code}}))
        return 1
    finally:
        if database is not None:
            await database.close()


def read_json(path: str) -> Any:
    with Path(path).open("rb") as handle:
        data = handle.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise VisualFailure("MANIFEST_TOO_LARGE")
    return json.loads(data)


def write_result(report: dict[str, Any], path: str | None) -> None:
    data = json.dumps(report, indent=2) + "\n"
    if path is None:
        print(data, end="")
        return
    # A frozen calibration or evaluation artifact must never be silently replaced.
    try:
        with Path(path).open("x") as handle:
            handle.write(data)
    except FileExistsError:
        raise VisualFailure("VISUAL_OUTPUT_EXISTS") from None
    print(json.dumps({"output": path}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "model", help="Download pinned local model weights (no API key)"
    )
    index = commands.add_parser("index", help="Manage the separate Atlas visual index")
    index.add_argument("--create", action="store_true")
    preparation = commands.add_parser(
        "prepare", help="Prepare temporary images into persisted descriptors"
    )
    preparation.add_argument("--location-id", required=True)
    preparation.add_argument("--limit", type=int, default=25)
    preparation.add_argument("--offset", type=int, default=0)
    preparation.add_argument(
        "--manifest", help="Explicit reference views for any object type"
    )
    evaluation = commands.add_parser(
        "benchmark", help="Compare global, regional and local baselines"
    )
    evaluation.add_argument("--manifest", required=True)
    evaluation.add_argument(
        "--policy", required=True, help="Frozen model/version and per-method thresholds"
    )
    evaluation.add_argument(
        "--split", choices=["calibration", "evaluation"], required=True
    )
    audit = commands.add_parser(
        "audit",
        help="Check photo coverage and split leakage without Atlas or inference",
    )
    audit.add_argument("--manifest", required=True)
    audit.add_argument("--output")
    fitting = commands.add_parser(
        "calibrate",
        help="Fit thresholds only on calibration photos and freeze the policy",
    )
    fitting.add_argument("--manifest", required=True)
    fitting.add_argument(
        "--policy",
        required=True,
        help="Model/version and fixed retrieval floors for calibration",
    )
    fitting.add_argument("--output", required=True)
    validation = commands.add_parser(
        "validate", help="Evaluate a frozen calibration artifact on held-out photos"
    )
    validation.add_argument("--manifest", required=True)
    validation.add_argument("--calibration", required=True)
    validation.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "prepare" and (not 1 <= args.limit <= 500 or args.offset < 0):
        parser.error("limit must be 1–500 and offset must be nonnegative")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
