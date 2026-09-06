# VisitAdvisor

VisitAdvisor is a location-aware knowledge platform that will help visitors
understand real-world places and objects through prepared, source-backed content.
The primary experience will be automatic discovery after recognition.

This repository implements **Phases 0 and 1**: a Python/FastAPI foundation,
MongoDB lifecycle management, and creation/retrieval of locations, objects,
and source references.
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
Startup idempotently creates the Phase 1 indexes after connecting. The Atlas
database user needs permission to create indexes and read/write the three
collections. Index initialization failure aborts startup with a sanitized error.
The health ping itself does not create collections or indexes.

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
startup/shutdown, health endpoints, API docs, configuration, error responses,
domain creation/retrieval, pagination, and relationship validation.
Real Atlas connectivity must be checked separately with a configured `.env`:

```bash
curl --fail http://localhost:8001/health
curl --fail http://localhost:8001/health/database
```

## Code layout and scope

- `app/main.py`: application factory and lifespan
- `app/core/`: settings, JSON logging, centralized public errors
- `app/db/mongodb.py`: async client and database dependency
- `app/api/`: health endpoints and the `/api/v1` domain router
- `app/modules/{locations,objects,sources}/`: models, repositories, services, routes
- `tests/`: isolated foundation tests
- `docs/`: product and architecture specifications

Repositories use the existing `get_database` dependency and async PyMongo client.
There is no frontend, AI integration, recognition, ingestion, authentication,
or background processing.

## Phase 1: manual domain data

Open http://localhost:8001/docs and use **Try it out** in this order:

1. `POST /api/v1/locations` with:

   ```json
   {
     "name": "Rijksmuseum",
     "slug": "rijksmuseum",
     "type": "museum",
     "city": "Amsterdam",
     "country": "Netherlands",
     "official_website": "https://www.rijksmuseum.nl/"
   }
   ```

2. Copy the returned `id`. Use it as `location_id` in
   `POST /api/v1/locations/{location_id}/sources` with:

   ```json
   {
     "title": "Rijksmuseum collection",
     "url": "https://www.rijksmuseum.nl/en/explore-the-collection/",
     "publisher": "Rijksmuseum",
     "type": "official_website"
   }
   ```

3. Copy the source `id`. Call `POST /api/v1/locations/{location_id}/objects`:

   ```json
   {
     "name": "The Night Watch",
     "type": "painting",
     "source_ids": ["REPLACE_WITH_SOURCE_ID"]
   }
   ```

   Repeat with names `The Milkmaid` and `The Jewish Bride`. These are manual
   sample records, not a curated knowledge dataset; facts/story/history remain
   empty until supported content is added in a later phase.

4. Retrieve the records using the endpoints below. Restart the app and retrieve
   the same IDs again to confirm persistence. Creation is not an upsert: reusing
   a location slug returns HTTP 409. Reuse existing IDs when repeating the flow.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/v1/locations` | Create location |
| GET | `/api/v1/locations` | List locations |
| GET | `/api/v1/locations/{location_id}` | Retrieve location |
| POST | `/api/v1/locations/{location_id}/objects` | Create object |
| GET | `/api/v1/locations/{location_id}/objects` | List location objects |
| GET | `/api/v1/objects/{object_id}` | Retrieve object |
| POST | `/api/v1/locations/{location_id}/sources` | Create source |
| GET | `/api/v1/locations/{location_id}/sources` | List location sources |
| GET | `/api/v1/sources/{source_id}` | Retrieve source |

Lists return JSON arrays, ordered by ID, with `limit` (default 20, maximum 100)
and `offset` (default 0). Creates return HTTP 201. Missing records return 404,
invalid requests/references return 422, and duplicate location slugs return 409.
All failures use the existing `error.code` / `error.message` envelope.

Indexes: unique `locations.slug`, `objects.location_id`, `sources.location_id`,
plus MongoDB's automatic `_id` indexes. No extra setup variables are required.
Normal tests use isolated collection doubles and never write to Atlas.

Manual verification on 2026-09-06 used real Atlas and the HTTP API on port 8001:
Rijksmuseum (`6a9dd4106d52c3bc3965869d`), one official source reference, and
The Night Watch, The Milkmaid, and The Jewish Bride were created. All five
records were retrieved unchanged after a full Uvicorn process restart. Required
indexes were inspected in Atlas and duplicate slug creation returned HTTP 409.
These sample records remain available for exploration in Swagger.
