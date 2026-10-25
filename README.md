# VisitAdvisor

The [recognition reliability work](docs/22-recognition-reliability.md) records the
latest retrieval changes, calibration data, manual selection and failed experiments.
The [real-time recognition guide](docs/21-realtime-recognition-pipeline.md) covers
the experimental type-aware camera pipeline, model readiness, bounded inference,
preview calibration and measured limitations.

The [internet-only 3D pilot](docs/23-3d-object-pilot.md) adds a sculpture photo lab
and visual evaluation report. It remains a candidate preview: measured 3D
reliability fails acceptance, and its fitted suggestion policy is not enabled.

The camera server now reads references from **MongoDB Atlas**, with process-local
reuse and periodic freshness checks. See [configuration and deployment](docs/18-atlas-camera-references.md).
The packaged-catalog instructions below describe the historical trial.

Artwork/room ranking fixes, opt-in geometry experiments and reproducible regression
commands are documented in [ticket #9 development](docs/20-artwork-room-confusion.md).
Independent acceptance remains pending; automatic visual identification stays disabled.

The current camera trial uses **server-side recognition**: selected 224 × 224 JPEG
crops are uploaded while scanning and processed in memory, without storage. The
phone downloads no model. See [server setup, deployment and limits](docs/17-server-camera-trial.md).
Earlier on-device camera instructions below describe the historical prototype.

VisitAdvisor is a location-aware knowledge platform that will help visitors
understand real-world places and objects through prepared, source-backed content.
The primary experience will be automatic discovery after recognition.

This repository implements **Phases 0–4, Phase 5 OCR, an experimental visual-recognition pilot, and a local live-camera prototype**: a Python/FastAPI foundation,
MongoDB lifecycle management, and creation/retrieval of locations, objects,
and source references, bounded imports from the official Rijksmuseum API, and
source-cited visitor content preparation, museum-filtered text retrieval, and uploaded-label OCR recognition.
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
Atlas `MONGODB_URI`. Allow your machine's public outbound IP in Atlas network
access. Compose passes `.env` to the API container; it is not included in the
image. For live camera recognition at `/live`, prepare `.local/live-camera/`
with `catalog.json` and `model.onnx` before starting Compose. Compose mounts this
private bundle read-only at `/app/.local/live-camera`; it is not copied into the
image. The rest of `.local/` stays on the host.

```bash
docker compose up --build
```

Compose mounts `app/` from the host and runs Uvicorn with reload enabled, so
Python source changes take effect without rebuilding. For subsequent starts,
use `docker compose up`; rebuild with `docker compose up --build` only after
changing dependencies or the Dockerfile.

Open http://localhost:8001/docs or check connectivity:

```bash
curl --fail http://localhost:8001/health/database
```

Stop with Ctrl+C, or run `docker compose down` from another terminal. The
container runs as a non-root user, installs only runtime dependencies, and
receives configuration at runtime. `.env` and the local virtual environment
are excluded from the build context. MongoDB remains in Atlas; Compose does not
start a local database.

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
The experimental `/live/` frontend processes the live camera on the device. It has
no image uploads, recording, authentication, or background processing.
Phase 5 accepts label image uploads and performs OCR; Phase 4 also accepts text.
Imports run through the CLI.

## Live camera prototype — iPhone 12 Pro / Safari

See [setup and limitations](docs/16-live-camera-prototype.md). This is an explicit
scope extension, not completion of Phase 5 recognition validation. It continuously
analyzes the center of the live view and shows tentative candidates. Camera pixels
stay on the phone. The current recorded-source demo covers **The Milkmaid** and
**The Jewish Bride**; an explicit Atlas export supports 2–12 selected objects.

```bash
.venv/bin/pip install -e '.[dev,visual,camera]'
npm ci
npm run camera:vendor
# Uses the cached DINOv2 weights; run the existing visual model setup if absent.
.venv/bin/python -m scripts.prepare_camera_demo
.venv/bin/uvicorn app.modules.live_camera.server:app --host 127.0.0.1 --port 8002 --no-access-log
```

Open `http://localhost:8002/` on the development computer. The standalone host
serves only the prototype and allowlisted downloads; it requires no Atlas connection
and does not expose the existing writable or paid API routes. A physical iPhone
requires trusted HTTPS; a LAN IP over ordinary HTTP cannot access its camera.
The guide includes a local certificate setup. No public deployment is performed.

Browser checks: `npm test`, then `npx playwright install --with-deps chromium webkit`
and `npm run test:browser`. The real-model browser test runs when a local bundle is
prepared; other browser tests use synthetic camera/worker fixtures. Keep the
existing Python checks as well.

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

## Phase 2: trusted Rijksmuseum import

The single connector uses the official [Rijksmuseum Search API and Linked Art
resolver](https://data.rijksmuseum.nl/tutorials/search/). No museum API key is
needed. It queries paintings and resolves up to 100 records by default; this
is a bounded sample, not a full collection mirror or a list of works currently
on display. Search results follow the provider's order and may change over time.

Activate the environment and install runtime dependencies:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

Create the Rijksmuseum location through Swagger if needed, using slug
`rijksmuseum`. Pass its ID to the CLI. For the existing sample destination:

```bash
python -m app.modules.ingestion \
  --location-id 6a9dd4106d52c3bc3965869d --limit 100
```

`--limit` accepts 1–500 and bounds the number of unique records attempted.
The command requires the same Atlas settings as the API; FastAPI itself does
not need to be running. It validates that the destination exists and has slug
`rijksmuseum`. No location is created or changed automatically.

Progress goes to stderr; a JSON report goes to stdout. To save the report:

```bash
python -m app.modules.ingestion \
  --location-id 6a9dd4106d52c3bc3965869d --limit 100 > import-report.json
```

The report contains `attempted`, `imported`, `updated`, `skipped`, `failed`, and
safe per-record error codes. `skipped` means unchanged normalized content.
Record failures do not stop subsequent records. A page failure ends the run
with completed counts preserved and a run-level error. Exit code is 0 for a
successful run, 1 for reported import/configuration/database failures, and 2
for invalid CLI arguments. An exhausted provider result set can yield fewer
records than requested. Rerun the same command to retry failures.

The connector sends sequential requests with 30-second timeouts and up to three
attempts for network errors, HTTP 429, and server errors. It follows only the
provider's search pages and resolves official object identifiers; it does not
crawl webpages, follow image links, or download image files.

### Persistence and evidence

- Each imported object and source has `import_provider=rijksmuseum` and an
  `external_id` equal to the official persistent object URI.
- Partial unique indexes on `(location_id, import_provider, external_id)` in
  both collections prevent duplicate imported objects and sources. Existing
  manual records without an import identity are unaffected.
- Each object references its own source record. The source retains the latest
  full Linked Art JSON under `raw_record`, its official URL, and `last_fetched_at`.
  This is canonical source evidence; no AI content is generated.
- Reimports refresh nonempty mapped source fields and preserve IDs/creation
  dates. Blank optional values cannot erase existing content. Aliases and source
  IDs are merged, and metadata is merged under `metadata.rijksmuseum`.
- Facts, story, history, other source references, and unrelated metadata are
  preserved. Source snapshots are latest-only; historical evidence versioning
  is deferred. A failed object write can leave a valid source record, reused on
  the next run. No existing data is deleted on failure.
- Names, aliases, creator attribution, date text (including multiple ranges),
  descriptions, accession numbers, and materials come from museum metadata.
  English is preferred where available, with source-language fallback.
- Manual objects without stable external IDs are not automatically matched by
  title or merged with imported records. That would risk merging distinct works.

After importing, restart any API process that predates Phase 2, then inspect:

```text
GET /api/v1/locations/{location_id}/objects?limit=100&offset=0
GET /api/v1/objects/{object_id}
GET /api/v1/sources/{source_id}
```

The existing API remains on port 8001. There is no public import endpoint.
To run the CLI in Docker, rebuild the image and override its command:

```bash
docker build -t visitadvisor:local .
docker run --rm --env-file .env visitadvisor:local \
  python -m app.modules.ingestion \
  --location-id 6a9dd4106d52c3bc3965869d --limit 100
```

Tests use synthetic records and mocked HTTP/database boundaries; normal
`pytest` does not call the museum or write to Atlas.

Live Phase 2 verification on 2026-09-06 imported 100 Rijksmuseum objects with
100 individual evidence sources into Atlas. A repeat CLI run reported
`imported=0`, `updated=0`, `skipped=100`, `failed=0`; object/source IDs remained
unchanged. After restarting FastAPI, the API returned all 100 imports alongside
the three manual Phase 1 objects and resolved their source evidence correctly.

## Phase 3: prepare visitor content

Phase 3 uses the OpenAI Responses API with structured output. The default model
is `gpt-4.1-mini-2025-04-14`, configurable through `LLM_MODEL`. Set
`OPENAI_API_KEY` in the ignored `.env` file. This key is required only by the
preparation CLI; existing API endpoints/imports continue to work without it.
Generation sends stored museum evidence to OpenAI and incurs API usage charges.
A ChatGPT subscription does not fund API usage.

Start with a three-object sample:

```bash
source .venv/bin/activate
python -m app.modules.knowledge \
  --location-id 6a9dd4106d52c3bc3965869d --limit 3
```

The CLI selects imported objects in ID order within the given location.
`--offset` defaults to 0; `--limit` accepts 1–100 and defaults to 3. Review the
sample against its source records before processing the full set:

```bash
python -m app.modules.knowledge \
  --location-id 6a9dd4106d52c3bc3965869d --limit 100
```

At most three calls run concurrently in the foreground process. Unchanged
source evidence, model, and prompt version are skipped without calling the LLM.
`--force` regenerates existing results. Progress is logged to stderr and a JSON
report goes to stdout. Exit code 1 indicates failure; 2 indicates bad arguments.
Quota/access/request errors abort after the current batch. Transient network,
rate-limit, and server errors receive at most three attempts. Exhausted credits
are not retried. Requests have a 90-second timeout and 3,000 output-token cap.

Read prepared content at:

```text
GET /api/v1/objects/{object_id}/experience
```

The existing object responses also include nullable `prepared_content`. An object
without prepared content returns `CONTENT_NOT_PREPARED` (404) from `/experience`.
Swagger is still at http://localhost:8001/docs. No public generation endpoint
was added. For Docker, rebuild the image and run the same module as the container
command with `--env-file .env`.

### Content and provenance

- Generated introduction, facts, story, history, and interesting fact live under
  `objects.prepared_content`, separate from canonical object fields and sources.
- Each nonempty section/fact contains citations with a source ID and an exact
  quotation. Source IDs must belong to the object and its location; quotations
  must occur in the supplied raw evidence. Sparse evidence produces null sections
  or fewer facts. A missing introduction is treated as insufficient evidence.
- Only stored `sources.raw_record` evidence is sent. Existing generated text is
  never used as evidence, and a URL alone is not sufficient. There is no browsing,
  tool use, image recognition, or generation from model memory by design.
- Each saved result records source IDs, evidence hash, provider, requested and
  returned model, prompt version, UTC generation time, and input/output tokens.
  Evidence quotations remain in the generated record if source snapshots refresh.
- Pydantic validates output structure. Citation checks establish that quoted text
  exists, **not** that every generated claim logically follows from it. Results
  are explicitly marked `review_status=unreviewed`; factual review is still needed.
- Refused, truncated, invalid, uncited, oversized-input, and failed generations
  never replace existing content. Evidence changes detected during generation
  reject the write. Atomic replacement guards against concurrent object changes.
- Source hash changes cause regeneration on the next CLI run; imports do not
  automatically regenerate content. Previously prepared content remains available
  until a successful replacement. No historical source snapshots are added.
- The small `ContentGenerator` protocol keeps provider-specific calls separate.
  A future local-model adapter can reuse validation/storage; no local runtime or
  alternative provider is implemented in this phase.

Normal tests use mocked providers and isolated data without real API calls.
Live content generation requires an API project with available credit. The model
and provider boundary are defined in `app/modules/knowledge/provider.py`.

Phase 3 verification on 2026-09-08: all 84 automated tests, Ruff lint and
format checks, and mypy pass. Live health/database checks and all three saved
sample experience endpoints return HTTP 200 on port 8001; missing and malformed
object IDs return 404 and 422 respectively. A repeat three-object preparation run
reports 0 generated, 3 skipped, and 0 failed, reusing saved content.

The live sample contains three generated objects using prompt `visitor-content-v2`.
Numbered evidence selection lets the application attach exact source quotations.
However, factual review found unsupported historical interpretations, an
unsupported authenticity claim from a signature, and a frame/object dimension
ambiguity. Exact citation validation does not guarantee claim support. All three
results remain `unreviewed`; the 100-object generation milestone remains pending
prompt/content corrections and another factual review.


## Phase 4: retrieval and text matching

Prepare museum identity embeddings and create the Atlas vector index:

```bash
.venv/bin/python -m app.modules.retrieval prepare \
  --location-id 6a9dd4106d52c3bc3965869d --limit 500
.venv/bin/python -m app.modules.retrieval index --create
.venv/bin/python -m app.modules.retrieval index
```

Wait for index status `ready` before testing semantic search. Repeating preparation
skips unchanged objects. Defaults: `EMBEDDING_MODEL=text-embedding-3-small`,
`EMBEDDING_DIMENSIONS=1536`, `VECTOR_INDEX_NAME=visitadvisor_identity_v1`.
The existing `OPENAI_API_KEY` is used; keep it in the ignored `.env` file.

```bash
curl -X POST http://localhost:8001/api/v1/recognition/match \
  -H 'Content-Type: application/json' \
  -d '{"location_id":"6a9dd4106d52c3bc3965869d","text":"SK C 1726","mode":"hybrid"}'

curl -X POST http://localhost:8001/api/v1/locations/6a9dd4106d52c3bc3965869d/search \
  -H 'Content-Type: application/json' \
  -d '{"text":"seascape made from tiny dots of pigment","mode":"semantic","limit":3}'
```

Matching returns `matched`, `ambiguous` (up to three candidates), or `no_match`.
Search and matching always filter by museum. Hybrid mode combines lexical signals
with Atlas vector similarity; unique exact identity matches can skip paid requests.
If semantic services are unavailable, hybrid responses report a lexical fallback;
semantic-only requests fail explicitly with 503. Scores are not probabilities.

Only canonical identity metadata and descriptions are embedded. Phase 3's
unreviewed stories remain separate, and its factual-quality issue is still open.
Phase 5 now requires label-free visual recognition, including partial views.
The existing OCR upload is only one component; see the
[visual pilot plan](docs/07-visual-recognition-pilot.md).

See [Phase 4 details](docs/05-retrieval-and-matching.md) for schema, limits, ranking,
index migration, failure handling, and the agreed roadmap.

Phase 4 verification (2026-09-09): 129 tests and all required static checks pass.
Atlas index is ready with 103 current identity embeddings. A repeat run skips all
103 with zero embedding tokens. Six live matching/search checks pass, covering
exact identity, OCR-like errors, semantic and cross-language queries, and no match.
See the Phase 4 document for the bounded test results and remaining limitations.


## Phase 5: current label upload API

The existing default is label OCR. An opt-in `mode=visual` now returns image-embedding
candidates without requiring readable text. Automatic visual identification remains
disabled pending independent evaluation. See the [Phase 5 visual pilot](docs/07-visual-recognition-pilot.md).

Update dependencies with `.venv/bin/pip install -e '.[dev]'`, then restart the API.
Use the **recognition** section in http://localhost:8001/docs, or:

```bash
curl -X POST http://localhost:8001/api/v1/recognition/image \
  -F 'location_id=6a9dd4106d52c3bc3965869d' \
  -F 'image=@/path/to/label.jpg;type=image/jpeg'
```

Send one JPEG, PNG, or WebP (up to 5 MiB and 16 megapixels), containing a clear
exhibit label. The API transcribes its text, matches within the selected museum,
and returns the object with its saved `prepared_content` when confident. Otherwise,
it returns up to three candidates or a rescan/no-match result. It does not generate
visitor content during a scan, and unreviewed content is explicitly marked.

The existing `OPENAI_API_KEY` powers OCR. Optional settings:
`OCR_MODEL=gpt-4.1-mini-2025-04-14` and `OCR_TIMEOUT_SECONDS=30`.
Uploads are metadata-stripped before provider submission and are not archived.
The OCR mode adds no camera UI and uses no image embeddings. See [Phase 5 details](docs/06-image-recognition.md)
for response fields, validation limits, errors, cost behavior, and verification.

## Phase 5: visual recognition pilot

Visual preparation now stores DINOv2 image/regional embeddings and a local-feature
baseline in separate Atlas collections. Reference images are temporary. Multiple
source-linked views may represent any object type, including a building; the first
live reference sample uses existing Rijksmuseum paintings.

After installing the optional `visual` dependencies and downloading the pinned
model, prepare references and the separate visual index. Set `VISUAL_ENABLED=true`
and send `mode=visual` to `/api/v1/recognition/image` for up to three candidates
without OCR. This mode never automatically identifies an object yet.

The [visual pilot guide](docs/07-visual-recognition-pilot.md) includes setup,
reference manifests for other object types, the benchmark workflow, and acceptance
gates. Public visitor photos now provide the full source-separated validation set;
a physical phone/network run remains the final deployment measurement.

The [calibration and validation guide](docs/09-visual-calibration-and-validation.md)
documents `visual audit`, `visual calibrate`, and `visual validate`, frozen policy
artifacts, the current real-photo results, and missing dataset coverage.

The [expanded validation run](docs/10-visual-expanded-validation.md) adds sculpture,
building, partial-view, and wrong-location cases. Full-view recognition passes the
pilot target on this sample, but building details and dataset/sample-size gates
still fail. Use the [field-photo checklist](docs/11-visual-field-photo-checklist.md)
to collect the remaining independent captures. Automatic visual identification
remains disabled.

The [v3 follow-up](docs/12-visual-expanded-validation-v3.md) expands building
references and the evaluation corpus. Regional partial-view top-three accuracy
reaches 8/10, but simulated automatic precision fails after the broader negatives
expose three false selections. At least 40 additional independent positive photos
and fresh held-out validation remain required.

The [online-photo acquisition decision](docs/13-visual-online-photo-acquisition.md)
records the no-new-photographer constraint, the first reviewed public-source batch,
strict acceptance rules, and the criterion for changing the pilot location.

The [v4 online validation](docs/14-visual-online-validation-v4.md) closes the
dataset gap with 110 photos and 20 evaluation identities. Regional embeddings pass
full and partial top-three targets at 18/20 and 33/40 with zero negative automatic
errors. The frozen policy makes only 11 automatic selections, below the required
30, so automatic identification remains disabled while broader calibration and a
new blind validation set are developed.

The [v5 source-separated validation](docs/15-visual-online-validation-v5.md) tests
the 75/25 global/regional ensemble on 180 photos, including a fresh 90-photo
held-out split from 90 unique source pages. Dataset and API checks pass, but the
regional method reaches only 85.3% automatic precision and makes four false
automatic selections on negatives. Automatic identification therefore remains
disabled; the next iteration must improve ranking and references before another
unseen validation run.
