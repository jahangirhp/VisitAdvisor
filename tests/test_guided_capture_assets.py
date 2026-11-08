import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
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


@pytest.mark.parametrize(
    "path",
    [
        "/admin/",
        "/admin/index.html",
        "/admin/admin.js?v=quick-20261107-1",
        "/admin/admin.css?v=quick-20261107-1",
        "/admin/quick-capture.mjs?v=quick-20261107-1",
        "/admin/guided-quality.mjs?v=quick-20261107-1",
    ],
)
def test_admin_html_and_scripts_revalidate_even_on_not_modified(path: str) -> None:
    client = TestClient(create_app())
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    unchanged = client.get(path, headers={"If-None-Match": response.headers["etag"]})
    assert unchanged.status_code == 304
    assert unchanged.headers["cache-control"] == "no-cache, must-revalidate"


def test_admin_entrypoint_and_capture_modules_use_matching_release_urls() -> None:
    import re

    client = TestClient(create_app())
    html = client.get("/admin/").text
    script_url = re.search(r'<script src="([^"]+)"', html).group(1)
    style_url = re.search(r'<link rel="stylesheet" href="([^"]+)"', html).group(1)
    version = parse_qs(urlsplit(script_url).query)["v"]
    assert parse_qs(urlsplit(style_url).query)["v"] == version
    script = client.get("/admin/" + script_url.removeprefix("./")).text
    module_url = re.search(r"import\('([^']+quick-capture[^']+)'\)", script)
    # A current page must never import the removed browser capture layout.
    assert module_url is not None
    assert parse_qs(urlsplit(module_url.group(1)).query)["v"] == version
    module = client.get("/admin/" + module_url.group(1).removeprefix("./")).text
    quality_url = re.search(r"from '([^']+guided-quality[^']+)'", module)
    assert quality_url is not None
    assert parse_qs(urlsplit(quality_url.group(1)).query)["v"] == version
