"""CPU-only SAM2 child; no source frames or embeddings survive each request."""

import base64
import hashlib
import io
import logging
import math
import os
import time
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from app.core.logging import configure_logging
from app.modules.admin.server_model import verify_model


def inference_worker(connection: Connection, model_directory: Path) -> None:
    # Hub access is disabled even if a future library changes local loading.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    configure_logging()
    logger = logging.getLogger(__name__)
    try:
        verify_model(model_directory)
        logger.info("Capture model artifacts verified")
        import torch
        from transformers import Sam2Model, Sam2Processor
        from transformers.utils import logging as model_logging

        model_logging.set_verbosity_error()
        logger.info("Capture model dependencies loaded")
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        processor = Sam2Processor.from_pretrained(
            model_directory, local_files_only=True, token=False
        )
        model = (
            Sam2Model.from_pretrained(
                model_directory,
                local_files_only=True,
                token=False,
                use_safetensors=True,
            )
            .to("cpu")
            .eval()
        )
        # Exercise lazy CPU kernels during application startup, using only a
        # generated blank image. The first real photo then uses the warm path.
        from PIL import Image

        warmup = io.BytesIO()
        Image.new("RGB", (64, 64), (127, 127, 127)).save(warmup, "JPEG")
        predict(
            processor,
            model,
            {"image": warmup.getvalue(), "point": [0.5, 0.5], "box": None},
        )
        warmup.close()
        logger.info("Capture model warmup complete")
        connection.send({"ready": True})
        result: dict[str, Any] | None = None
        while True:
            message = connection.recv()
            if message is None:
                break
            try:
                result = predict(processor, model, message)
                connection.send({"ok": result})
            except Exception:
                connection.send({"error": "CAPTURE_ANALYSIS_UNAVAILABLE"})
            finally:
                # Only weights remain between calls, never a frame or prompt.
                message = result = None
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send({"error": "CAPTURE_ANALYSIS_UNAVAILABLE"})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


def predict(processor: Any, model: Any, message: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import torch
    from PIL import Image

    started = time.perf_counter()
    with Image.open(io.BytesIO(message["image"])) as source:
        image = source.convert("RGB")
    width, height = image.size
    x, y = message["point"]
    prompt: dict[str, Any] = {
        "input_points": [[[[x * (width - 1), y * (height - 1)]]]],
        "input_labels": [[[1]]],
    }
    if message["box"] is not None:
        x1, y1, x2, y2 = message["box"]
        prompt["input_boxes"] = [[[x1 * width, y1 * height, x2 * width, y2 * height]]]
    inputs = processor(images=image, **prompt, return_tensors="pt")
    with torch.inference_mode():
        outputs = model(**inputs, multimask_output=True)
        masks = processor.post_process_masks(
            outputs.pred_masks.cpu(), inputs["original_sizes"]
        )[0][0].numpy()
        scores = outputs.iou_scores[0, 0].cpu().numpy().tolist()
    candidates = []
    for index, (mask, score) in enumerate(zip(masks[:3], scores[:3], strict=True)):
        if not math.isfinite(score):
            continue
        foreground = mask.astype(bool)
        ys, xs = np.where(foreground)
        if not len(xs):
            continue
        binary = Image.fromarray(foreground.astype(np.uint8) * 255)
        output = io.BytesIO()
        binary.save(output, format="PNG")
        data = output.getvalue()
        if len(data) > 512 * 1024:
            continue
        candidates.append(
            {
                "id": f"mask-{index}",
                "score": float(score),
                "target_box": [
                    float(xs.min()) / width,
                    float(ys.min()) / height,
                    (float(xs.max()) + 1) / width,
                    (float(ys.max()) + 1) / height,
                ],
                "mask_png_base64": base64.b64encode(data).decode("ascii"),
                "mask_sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "candidates": candidates,
        "inference_ms": (time.perf_counter() - started) * 1000,
    }
