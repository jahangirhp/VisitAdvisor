"""Stage the camera server and private model; references load from Atlas."""

import importlib.metadata
import shutil
from pathlib import Path


def main() -> None:
    root = Path(".local/live-camera")
    output = root / "cloud-run-server"
    output.mkdir(parents=True, exist_ok=True)
    files = [
        "app/__init__.py",
        "app/modules/__init__.py",
        "app/modules/live_camera/__init__.py",
        "app/modules/live_camera/server.py",
        "app/modules/live_camera/inference.py",
        "app/modules/live_camera/geometry.py",
        "app/modules/live_camera/references.py",
        "app/modules/live_camera/pipeline.py",
        "app/modules/live_camera/regions.py",
        "app/modules/live_camera/policy.py",
        "app/modules/live_camera/export.py",
        "app/modules/visual/repository.py",
        "app/core/__init__.py",
        "app/core/config.py",
        "app/db/__init__.py",
        "app/db/mongodb.py",
        "app/modules/visual/__init__.py",
        "app/modules/visual/features.py",
    ]
    files += [
        f"app/modules/live_camera/static/{name}"
        for name in (
            "index.html",
            "camera.css",
            "camera.mjs",
            "capture.mjs",
            "recognition.mjs",
            "MODEL_LICENSE",
            "ORT_LICENSE",
        )
    ]
    for name in files:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(name, destination)
    (output / "bundle").mkdir(exist_ok=True)
    # Remove the obsolete snapshot from earlier staging runs.
    (output / "bundle" / "catalog.json").unlink(missing_ok=True)
    for name in ("model-metadata.json", "model.onnx", "MODEL_LICENSE"):
        shutil.copy2(root / name, output / "bundle" / name)
    (output / "requirements.txt").write_text(
        "".join(
            f"{name}=={importlib.metadata.version(name)}\n"
            for name in (
                "fastapi",
                "uvicorn",
                "pillow",
                "numpy",
                "onnxruntime",
                "opencv-python-headless",
                "pymongo",
                "pydantic-settings",
                "dnspython",
            )
        )
    )
    (output / ".gcloudignore").write_text("__pycache__/\n*.pyc\n")
    (output / "Dockerfile").write_text("""FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LIVE_CAMERA_BUNDLE=/app/bundle
ENV CAMERA_APP=app.modules.live_camera.server:app PORT=8080
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY bundle ./bundle
USER 65532:65532
EXPOSE 8080
CMD exec uvicorn $CAMERA_APP --host 0.0.0.0 --port $PORT --no-access-log
""")
    print(output)


if __name__ == "__main__":
    main()
