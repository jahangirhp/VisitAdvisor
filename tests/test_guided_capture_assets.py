import hashlib
import io
import json
from pathlib import Path

import pytest

from scripts import prepare_guided_capture as assets


def test_bad_download_preserves_existing_asset(tmp_path: Path) -> None:
    target = tmp_path / "model.task"
    target.write_bytes(b"original")
    with pytest.raises(ValueError, match="checksum mismatch"):
        assets.write_verified(
            target, b"changed", hashlib.sha256(b"original").hexdigest()
        )
    assert target.read_bytes() == b"original"
    assert not target.with_suffix(".task.tmp").exists()


def test_runtime_version_is_pinned_before_any_asset_write(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(json.dumps({"version": "next"}))
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="1.2.2"):
        assets.prepare(package, destination)
    assert not destination.exists()


def test_mismatched_installed_runtime_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"version": assets.RUNTIME_VERSION})
    )
    (package / "vision_bundle.mjs").write_bytes(b"tampered")
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="checksum mismatch"):
        assets.prepare(package, destination)
    assert not destination.exists()


def test_model_metadata_names_immutable_version() -> None:
    model = assets.manifest()["model"]
    assert isinstance(model, dict)
    assert "/float32/1/" in model["source"]
    assert "latest" not in model["source"]
    assert model["sha256"] == assets.MODEL_SHA256
    assert model["bytes"] == assets.MODEL_SIZE


def test_downloaded_runtime_archive_is_hash_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assets, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="archive checksum mismatch"):
        assets.runtime_bytes(tmp_path / "absent-package")
