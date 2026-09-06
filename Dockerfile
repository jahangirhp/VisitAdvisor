FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && groupadd --system visitadvisor \
    && useradd --system --gid visitadvisor --no-create-home visitadvisor

USER visitadvisor
EXPOSE 8001

CMD ["python", "-m", "app.main"]
