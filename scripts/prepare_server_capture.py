"""Prepare pinned SAM2 weights explicitly; no application requests download them."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import urlopen

from app.modules.admin.server_model import (
    MODEL_FILES,
    MODEL_ID,
    MODEL_REVISION,
    manifest,
    verify_model,
)


def prepare(destination: Path, source: Path | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in MODEL_FILES.items():
        target = destination / name
        if target.is_file() and target.stat().st_size == size:
            if hashlib.file_digest(target.open("rb"), "sha256").hexdigest() == digest:
                continue
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            if source is not None:
                with (source / name).open("rb") as input_file:
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(input_file, output, 1024 * 1024)
            else:
                url = (
                    f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{name}"
                )
                with urlopen(url, timeout=120) as response:
                    with temporary.open("wb") as output:
                        remaining = size + 1
                        while remaining:
                            chunk = response.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            output.write(chunk)
                            remaining -= len(chunk)
            with temporary.open("rb") as artifact:
                actual = hashlib.file_digest(artifact, "sha256").hexdigest()
            if temporary.stat().st_size != size or actual != digest:
                raise ValueError("Pinned server capture artifact verification failed.")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (destination / "manifest.json").write_text(json.dumps(manifest(), indent=2) + "\n")
    verify_model(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination", type=Path, default=Path(".local/server-capture")
    )
    parser.add_argument("--source", type=Path, help="Copy verified offline artifacts")
    parser.add_argument(
        "--check", action="store_true", help="Verify without downloading"
    )
    args = parser.parse_args()
    try:
        if not args.check:
            prepare(args.destination, args.source)
        verify_model(args.destination)
    except (OSError, ValueError):
        parser.exit(1, "Server capture model unavailable; prepare verified weights.\n")
    print(f"Verified {MODEL_ID} at revision {MODEL_REVISION}.")


if __name__ == "__main__":
    main()
