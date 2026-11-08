"""Prepare pinned, same-origin MediaPipe assets for guided admin capture.

Run python -m scripts.prepare_guided_capture. An installed pinned npm package is
preferred; Python-only builds use the hash-verified public npm archive. Only this
setup step downloads assets. Capture sessions make no external model/API calls.
Large model/runtime files stay in .local rather than the application source tree.
"""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path
from urllib.request import urlopen

RUNTIME_VERSION = "1.2.2"
RUNTIME_URL = (
    "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.2.2.tgz"
)
RUNTIME_SHA512 = (
    "67b0629319d3be52a073777f370f8b57a074c720000b161e0071d4bbfc54866d5"
    "257b8e51316bd337edb7a217fc19168cb41fe72cd4f9484f6678926d365041d"
)
RUNTIME_ARCHIVE_SIZE = 8_743_225
MODEL_NAME = "mediapipe-magic-touch-legacy-float32-1"
MODEL_FILENAME = "magic_touch.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "interactive_segmenter/magic_touch/float32/1/magic_touch.tflite"
)
MODEL_SHA256 = "e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25"
MODEL_SIZE = 6_227_884
RUNTIME_HASHES = {
    "vision_bundle.mjs": (
        "b65acb59b370cd6561afd9d04c29ed7a87e7ed6386922ac1b263af1f1ad06b7d"
    ),
    "wasm/vision_wasm_internal.js": (
        "f2b9c17131ea79716ec6bf07b117a087b2749a6f8a1e2f1f2e77a9382092db5b"
    ),
    "wasm/vision_wasm_internal.wasm": (
        "f5bd25147ac89d506074293ce2675969b10e827180db95a0d9689c2ed80764ab"
    ),
    "wasm/vision_wasm_module_internal.js": (
        "72fba0ac8a4034ce9d63f68389df1dead52069409ea621688eedf97f01d4362f"
    ),
    "wasm/vision_wasm_module_internal.wasm": (
        "b8927a2eebfe80dd98a426a210a9e820c8215168c06f3bcf5d4d09080f623db5"
    ),
}
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def verified_bytes(path: Path, expected_hash: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError(f"Pinned asset checksum mismatch: {path.name}")
    return data


def write_verified(destination: Path, data: bytes, expected_hash: str) -> None:
    """Never replace a usable asset with an incomplete or unverified download."""
    if hashlib.sha256(data).hexdigest() != expected_hash:
        raise ValueError(f"Pinned asset checksum mismatch: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)


def manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "runtime": {
            "package": "@mediapipe/tasks-vision",
            "version": RUNTIME_VERSION,
            "license": "Apache-2.0",
            "source": "https://github.com/google-ai-edge/mediapipe",
            "archive": RUNTIME_URL,
            "archive_sha512": RUNTIME_SHA512,
            "files": RUNTIME_HASHES,
        },
        "model": {
            "name": MODEL_NAME,
            "filename": MODEL_FILENAME,
            "source": MODEL_URL,
            "sha256": MODEL_SHA256,
            "bytes": MODEL_SIZE,
            "model_card": (
                "https://storage.googleapis.com/mediapipe-assets/"
                "Model%20Card%20MagicTouch.pdf"
            ),
        },
        "delegate": "CPU",
        "mask_threshold": 0.5,
    }


def runtime_bytes(package: Path) -> dict[str, bytes]:
    if (package / "package.json").is_file():
        metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
        if metadata.get("version") != RUNTIME_VERSION:
            raise ValueError(f"Install @mediapipe/tasks-vision@{RUNTIME_VERSION} first")
        return {
            name: verified_bytes(package / name, expected)
            for name, expected in RUNTIME_HASHES.items()
        }
    # The Python-only production image does not need Node/npm to vendor assets.
    with urlopen(RUNTIME_URL, timeout=120) as response:
        archive_bytes = response.read(RUNTIME_ARCHIVE_SIZE + 1)
    if len(archive_bytes) != RUNTIME_ARCHIVE_SIZE or (
        hashlib.sha512(archive_bytes).hexdigest() != RUNTIME_SHA512
    ):
        raise ValueError("Pinned MediaPipe archive checksum mismatch")
    runtime = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        # Read only explicit members. Never extract archive paths or symlinks.
        for name, expected in RUNTIME_HASHES.items():
            member = archive.getmember(f"package/{name}")
            if not member.isfile() or member.size > 16_000_000:
                raise ValueError("Invalid MediaPipe runtime archive member")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Missing MediaPipe runtime asset")
            data = source.read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Pinned asset checksum mismatch: {name}")
            runtime[name] = data
    return runtime


def prepare(package: Path, destination: Path, model_file: Path | None = None) -> None:
    # Validate the complete runtime before writing any of its files.
    runtime = runtime_bytes(package)
    model_target = destination / MODEL_FILENAME
    if model_file is not None:
        model = verified_bytes(model_file, MODEL_SHA256)
    elif model_target.is_file():
        model = verified_bytes(model_target, MODEL_SHA256)
    else:
        with urlopen(MODEL_URL, timeout=120) as response:
            # Bound memory and detect truncated/changed model resources.
            model = response.read(MODEL_SIZE + 1)
    if len(model) != MODEL_SIZE:
        raise ValueError("Pinned segmentation model size mismatch")
    if hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise ValueError("Pinned segmentation model checksum mismatch")
    for name, data in runtime.items():
        write_verified(destination / name, data, RUNTIME_HASHES[name])
    write_verified(model_target, model, MODEL_SHA256)
    (destination / "manifest.json").write_text(
        json.dumps(manifest(), indent=2) + "\n", encoding="utf-8"
    )


def check(destination: Path) -> None:
    for name, expected in RUNTIME_HASHES.items():
        verified_bytes(destination / name, expected)
    model = verified_bytes(destination / MODEL_FILENAME, MODEL_SHA256)
    if len(model) != MODEL_SIZE:
        raise ValueError("Pinned segmentation model size mismatch")
    if json.loads((destination / "manifest.json").read_text()) != manifest():
        raise ValueError("Guided capture asset manifest mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=PROJECT_ROOT / ".local/guided-capture"
    )
    parser.add_argument("--model-file", type=Path, help="Use a verified offline copy")
    parser.add_argument(
        "--check", action="store_true", help="Check without downloading"
    )
    args = parser.parse_args()
    try:
        if not args.check:
            prepare(
                PROJECT_ROOT / "node_modules/@mediapipe/tasks-vision",
                args.destination,
                args.model_file,
            )
        check(args.destination)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Guided capture assets unavailable: {exc}\n")
    print(
        f"Verified {MODEL_NAME} and MediaPipe {RUNTIME_VERSION} in {args.destination}"
    )


if __name__ == "__main__":
    main()
