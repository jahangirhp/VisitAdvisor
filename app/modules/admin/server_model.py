"""Pinned public SAM2 image weights; preparation is separate from requests."""

import hashlib
import json
from pathlib import Path

MODEL_ID = "facebook/sam2.1-hiera-tiny"
MODEL_REVISION = "de431c4043854a71d8101e17995dfe596bf101a5"
MODEL_FILES = {
    "config.json": (
        5695,
        "860aff9751b139d83a4ad7df1e5535416fded533e0ead02625edbefcb9953cce",
    ),
    "model.safetensors": (
        155908064,
        "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7",
    ),
    "preprocessor_config.json": (
        683,
        "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d",
    ),
    "processor_config.json": (
        95,
        "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d",
    ),
}
MODEL_SHA256 = MODEL_FILES["model.safetensors"][1]


def manifest() -> dict[str, object]:
    return {
        "version": "server-capture-model-v1",
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "license": "Apache-2.0",
        "files": {
            name: {"bytes": size, "sha256": digest}
            for name, (size, digest) in MODEL_FILES.items()
        },
    }


def verify_model(directory: Path) -> None:
    """Never accept changed weights or invoke model-hub downloads at runtime."""
    for name, (size, digest) in MODEL_FILES.items():
        path = directory / name
        if path.stat().st_size != size:
            raise ValueError("Server capture model size mismatch.")
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            raise ValueError("Server capture model checksum mismatch.")
    if json.loads((directory / "manifest.json").read_text()) != manifest():
        raise ValueError("Server capture model manifest mismatch.")
