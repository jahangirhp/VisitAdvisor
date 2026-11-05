FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# The visual build pins the packages that are part of the reference-set identity,
# so embeddings match the Atlas references (see constraints-visual.txt).
ARG EXTRAS=camera-server
ARG TORCH_INDEX_URL=
ARG CONSTRAINTS=

COPY pyproject.toml constraints-visual.txt ./
COPY app ./app
COPY scripts/prepare_guided_capture.py ./scripts/prepare_guided_capture.py
RUN python scripts/prepare_guided_capture.py
RUN pip install --no-cache-dir ${TORCH_INDEX_URL:+--extra-index-url $TORCH_INDEX_URL} \
        ${CONSTRAINTS:+-c $CONSTRAINTS} ".[${EXTRAS}]" \
    && groupadd --system visitadvisor \
    && useradd --system --gid visitadvisor --no-create-home visitadvisor

USER visitadvisor
EXPOSE 8001

CMD ["python", "-m", "app.main"]
