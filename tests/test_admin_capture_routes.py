from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.admin import assets


def test_capture_assets_use_an_allowlist_and_correct_wasm_type(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "ASSET_ROOT", tmp_path)
    (tmp_path / "wasm").mkdir()
    (tmp_path / "wasm/vision_wasm_module_internal.wasm").write_bytes(b"wasm")
    (tmp_path / "private.txt").write_text("private")
    client = TestClient(create_app())
    response = client.get("/admin/capture-assets/wasm/vision_wasm_module_internal.wasm")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/wasm"
    assert response.content == b"wasm"
    assert client.get("/admin/capture-assets/private.txt").status_code == 404
    assert client.get("/admin/capture-assets/manifest.json").status_code == 404
    assert client.get("/admin/capture-assets/%2e%2e/private.txt").status_code == 404


def test_capture_worker_blocks_external_connections():
    client = TestClient(create_app())
    response = client.get("/admin/guided-segmenter.worker.mjs")
    assert response.status_code == 200
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert "wasm-unsafe-eval" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-cache"
