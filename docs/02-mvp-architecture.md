# VisitAdvisor — MVP System Architecture

**Version:** 0.1  
**Status:** Revised MVP scope — 2026-09-11

## 1. Architecture Goal

The MVP architecture should prove the full product flow with the fewest moving parts possible.

The system has two logical responsibilities:

1. **Knowledge Preparation** — import and prepare information before a visit.
2. **Visitor Runtime** — identify an object/place and return prepared content quickly.

These are logical boundaries, not separate microservices in the MVP.

## 2. High-Level MVP Architecture

```text
                 KNOWLEDGE PREPARATION

        Known museum/API/website source
                    ↓
             Ingestion module
                    ↓
            Parse / normalize
                    ↓
              LLM processing
           ┌────────┴────────┐
           ↓                 ↓
   Structured object     Prepared content
           │                 │
           └────────┬────────┘
                    ↓
              Embeddings
                    ↓
             MongoDB Atlas
      ┌─────────────┼─────────────┐
      ↓             ↓             ↓
  locations       objects    knowledge_chunks
                    │
                    ↓
                 sources

════════════════ VISITOR RUNTIME ════════════════

               Next.js / PWA
                    ↓
              Camera / Image
                    ↓
          Visual matching + optional OCR
                    ↓
             Object Matcher
                    ↓
              MongoDB Atlas
                    ↓
         Prepared Object Content
                    ↓
       Facts + Story + History
```

## 3. Recommended MVP Stack

| Area | MVP Choice |
|---|---|
| Frontend | Next.js + TypeScript, mobile-first PWA |
| Backend | Python + FastAPI |
| API style | REST initially |
| Main database | MongoDB Atlas |
| Semantic search | Atlas Vector Search |
| AI processing | External LLM API |
| Embeddings | External embedding API |
| OCR | External OCR/vision capability |
| File storage | Not required initially unless source files must be retained |
| Repository | Git monorepo |
| Local environment | Linux or Windows + WSL2 |
| Containerization | Docker optional but recommended |

## 4. Deployment Shape

For the first version, use a **modular monolith**.

```text
visitadvisor/
├── apps/
│   ├── web/            # Next.js visitor/admin UI
│   └── api/            # Python / FastAPI API
│
├── packages/
│   ├── database/
│   ├── ingestion/
│   ├── ai/
│   ├── recognition/
│   └── shared/
│
└── docs/
```

A separate background worker is **not required on day one**.

Long-running ingestion can initially be triggered via:

- CLI command, or
- protected admin endpoint

Introduce a queue/worker only when processing time, retries, concurrency, or reliability require it.

## 5. Core Runtime Components

### 5.1 Visitor Web App

Responsibilities:

- select location
- open camera/upload image
- send image/text for recognition
- display recognized entity
- show facts/story/history
- show alternate candidates when recognition is uncertain

### 5.2 Python / FastAPI API

Responsibilities:

- location APIs
- object APIs
- ingestion trigger
- OCR integration
- matching logic
- knowledge retrieval
- AI provider integration

### 5.3 Ingestion Module

Responsibilities:

```text
Known Source
   ↓
Fetch
   ↓
Parse
   ↓
Normalize
   ↓
LLM Extract
   ↓
Store
   ↓
Generate Content
   ↓
Embed
```

Only one source connector is required for the first location.

### 5.4 Recognition Module

MVP recognition targets **visual reference matching with optional OCR**.
The implementation supports OCR plus an opt-in visual candidate preview.
`07-visual-recognition-pilot.md` specifies the local image encoder, separate Atlas
visual index, and evaluation gates. Reference preparation runs before visits;
reference images are transient, and descriptors persist in Atlas.

Signals may include:

- selected `locationId`
- extracted title
- aliases
- artist/creator
- year/date
- accession/object number
- semantic similarity

Example:

```text
OCR:
"Rembrandt ... Nachtwacht ... 1642"

locationId:
rijksmuseum

        ↓
Candidate search
        ↓
The Night Watch       0.96
The Jewish Bride      0.39
Self Portrait         0.22
        ↓
Return best match
```

Label-free full-object and partial-view recognition must be evaluated before
claiming the recognition MVP is complete. Start with one museum and a bounded
reference collection across object types; do not infer accuracy for unseen
viewpoints. Multiple references can represent one object, including a building.

### 5.5 MongoDB Atlas

Use one Atlas deployment for:

- structured entities
- source metadata
- prepared visitor content
- vectorized knowledge

Do not introduce a separate vector database in the MVP.

## 6. Minimal Collections

```text
locations
objects
sources
knowledge_chunks
```

Detailed schema is defined in `03-data-model.md`.

## 7. Search Strategy

Use two types of matching where useful:

### Exact / Lexical Matching

Useful for OCR output such as:

- title
- alias
- accession number
- artist
- year

### Semantic Matching

Useful when OCR is incomplete or wording differs.

Store embeddings in `knowledge_chunks` and filter retrieval by `locationId`.

Phase 4 combines lexical and vector results with a simple weighted ranker.
See `05-retrieval-and-matching.md` for the implemented contract and limits.

## 8. Source Traceability

At minimum:

```text
Source
  ↓
Object
  ↓
Prepared content
```

Objects and generated content must keep `sourceIds` or equivalent references.

Detailed fact-level provenance can be introduced later if required by quality testing.

## 9. Minimal API Surface

Suggested initial endpoints:

```http
POST /locations
GET  /locations
GET  /locations/:id

POST /locations/:id/import
GET  /locations/:id/objects

GET  /objects/:id
GET  /objects/:id/experience

POST /recognition/ocr
POST /recognition/match
```

These endpoints are provisional; implementation can evolve without introducing a formal API gateway.

## 10. Failure Strategy

For the MVP:

- log failed source imports
- allow an import to be rerun
- avoid duplicate objects using stable external IDs when available
- store source URL/ID with imported records
- never delete working location data automatically because one refresh failed

## 11. Security Baseline

Before external testing:

- secrets must come from environment configuration
- admin import routes must not be publicly writable
- AI/API credentials must never reach the browser
- validate uploaded files/images
- restrict request size
- basic API rate limiting can be added when externally exposed

## 12. What Not to Build Yet

Do not add the following to the MVP architecture unless a proven need appears:

- microservices
- Kubernetes
- Kafka
- Redis/BullMQ before background-job needs appear
- Elasticsearch
- separate vector DB
- service mesh
- complex API gateway
- automatic source discovery
- reference-image repository at scale
- indoor positioning
- distributed package/version infrastructure
- custom model hosting

## 13. Architecture Evolution Trigger

Add a worker/queue when one or more of these become true:

- imports regularly exceed interactive request timeouts
- multiple locations must be processed concurrently
- LLM/API calls require controlled retries
- ingestion must resume after crashes
- scheduled refreshes are introduced

Split services only when independent scaling, team ownership, deployment, or reliability requirements justify it.

## 14. Architectural Principle

```text
Do beforehand:
collection + AI extraction + story generation + embeddings

Do at visit time:
Visual matching + optional OCR + retrieval + display
```

The visitor runtime must remain much simpler and faster than the knowledge-preparation path.
