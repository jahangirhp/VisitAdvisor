# VisitAdvisor

VisitAdvisor is a location-aware knowledge platform that will help visitors
understand real-world places and objects through prepared, source-backed content.
The primary experience will be automatic discovery after recognition.

This repository implements **Phase 0 only**: a Python/FastAPI foundation,
MongoDB lifecycle management, health endpoints, and development tooling.
The documents in `docs/` describe the broader MVP; they are not all implemented.

## Requirements

- Python 3.12+
- MongoDB Atlas account and a deployment with database credentials and network
  access configured for your machine
- Linux or Windows with WSL2 Ubuntu

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set `MONGODB_URI` in `.env` to your Atlas connection string, including database
user credentials. Never commit this file. `MONGODB_DATABASE` defaults to
`visitadvisor`.

```bash
uvicorn app.main:app --reload --port 8001
```

Endpoints:

- GET http://localhost:8001/health
- GET http://localhost:8001/health/database
- GET http://localhost:8001/docs

The Uvicorn CLI defaults to `127.0.0.1:8000`, so include `--port 8001`
to avoid conflicts with other projects.
To use `APP_HOST` and `APP_PORT` from centralized settings instead, run:

```bash
python -m app.main
```

`APP_ENV` defaults to `development` and is available for future environment-specific
behavior; error responses remain sanitized in all environments.

Startup validates configuration and verifies MongoDB connectivity before serving
requests. Invalid configuration or a failed database connection aborts startup.
Database operations have a five-second timeout. One async PyMongo client is shared
for each application lifespan and closed on shutdown, including failed startup.
The health ping does not create collections or indexes.

`/health` reports process liveness. `/health/database` checks current database
connectivity and returns HTTP 503 when unavailable:

```json
{"error": {"code": "DATABASE_UNAVAILABLE", "message": "Database is unavailable."}}
```

Application logs are JSON. Database details, credentials, and raw exception
messages are excluded from application logs and public errors.

## Docker

Docker can run the FastAPI backend while MongoDB remains in Atlas. A local
Python installation is not required for this option; Docker Engine must be
running and accessible to your user.

Create `.env` from `.env.example` if it does not already exist, then set your
Atlas `MONGODB_URI`. Use unquoted `KEY=value` entries for Docker's `--env-file`.
Allow your machine's public outbound IP in Atlas network access.

```bash
docker build -t visitadvisor:local .
docker run --rm --name visitadvisor \
  --env-file .env \
  -e APP_HOST=0.0.0.0 -e APP_PORT=8001 \
  -p 127.0.0.1:8001:8001 \
  visitadvisor:local
```

Open http://localhost:8001/docs or check connectivity:

```bash
curl --fail http://localhost:8001/health/database
```

Stop with Ctrl+C, or `docker stop visitadvisor` from another terminal. Rebuild
the image after code changes. The container runs as a non-root user, installs
only runtime dependencies, and receives configuration at runtime. `.env` and
the local virtual environment are excluded from the build context.

If Docker reports permission denied for `/var/run/docker.sock`, run these
commands from a terminal with Docker access (for example, use `sudo docker`
on Linux if your account has sudo access).

## Development checks

```bash
pytest
ruff check .
ruff format --check .
mypy app
```

Tests mock the MongoDB boundary and require no Atlas credentials. They exercise
startup/shutdown, health endpoints, API docs, configuration, and error responses.
Real Atlas connectivity must be checked separately with a configured `.env`:

```bash
curl --fail http://localhost:8001/health
curl --fail http://localhost:8001/health/database
```

## Code layout and scope

- `app/main.py`: application factory and lifespan
- `app/core/`: settings, JSON logging, centralized public errors
- `app/db/mongodb.py`: async client and database dependency
- `app/api/`: health endpoints and an empty `/api/v1` router
- `app/modules/locations/`: placeholders for Phase 1
- `tests/`: isolated foundation tests
- `docs/`: product and architecture specifications

Future domain routes can be mounted in `app/api/router.py` under `/api/v1` and
use `get_database` from `app/db/mongodb.py`. No domain schemas or indexes are
established yet. There is no frontend, AI integration, recognition, ingestion,
authentication, background processing, or other Phase 1+ functionality.
