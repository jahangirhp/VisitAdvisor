"""Serve only the pinned, public browser segmentation assets."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)
ASSET_ROOT = Path(".local/guided-capture")
ASSETS = {
    "manifest.json": "application/json",
    "vision_bundle.mjs": "text/javascript",
    "magic_touch.tflite": "application/octet-stream",
    "wasm/vision_wasm_module_internal.js": "text/javascript",
    "wasm/vision_wasm_module_internal.wasm": "application/wasm",
    "wasm/vision_wasm_internal.js": "text/javascript",
    "wasm/vision_wasm_internal.wasm": "application/wasm",
    "wasm/vision_wasm_nosimd_internal.js": "text/javascript",
    "wasm/vision_wasm_nosimd_internal.wasm": "application/wasm",
}


@router.get("/admin/capture-assets/{filename:path}")
async def capture_asset(filename: str) -> FileResponse:
    if filename not in ASSETS or not (ASSET_ROOT / filename).is_file():
        raise HTTPException(status_code=404, detail="Capture asset unavailable")
    return FileResponse(
        ASSET_ROOT / filename,
        media_type=ASSETS[filename],
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/admin/guided-segmenter.worker.mjs")
async def capture_worker() -> FileResponse:
    return FileResponse(
        Path(__file__).parent / "static/guided-segmenter.worker.mjs",
        media_type="text/javascript",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; "
                "script-src 'self' 'wasm-unsafe-eval'"
            ),
            "Cache-Control": "no-cache",
        },
    )
