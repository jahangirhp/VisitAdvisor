# Phase 4 — Retrieval and matching foundation

## Agreed roadmap

- Phase 3: AI-prepared facts, story, and history. Its factual review remains open.
- Phase 4: text embeddings, Atlas Vector Search, hybrid retrieval, and matching
  already-extracted OCR text within the selected museum.
- Phase 5 (revised 2026-09-11): visual recognition without labels, including
  partial views, with optional OCR and saved Phase 3 content. Show up to three
  candidates when uncertain. The OCR upload and experimental visual candidate
  preview exist; the benchmark and remaining acceptance gates are documented
  in `07-visual-recognition-pilot.md`.

This roadmap supersedes the earlier camera-first paragraph in the AI document.
Phase 4 does not accept images, extract OCR, or add a frontend.

## Search data and preparation

`python -m app.modules.retrieval prepare --location-id ID --limit 500`

One `identity` chunk per object/model/dimension is stored in `knowledge_chunks`.
The deterministic text contains canonical name, aliases, creator, year, type,
identifiers (including `metadata.rijksmuseum.object_number`), and description.
Generated `prepared_content`, facts, stories, and history are excluded. This allows
matching to work while the Phase 3 factual-quality review is still pending.

Each chunk retains `location_id`, `object_id`, `source_ids`, text, content hash,
text version, embedding provider/model/dimensions, origin, language (`und`, because
source metadata may mix languages), and creation/update timestamps. The origin is
`canonical_object`; canonical objects and sources are never rewritten. A source ID
must exist in the same museum before embedding. These references identify the
canonical record's provenance; Phase 4 does not invent new fact-level citations.

Preparation uses the dedicated `text-embedding-3-small` model, 1536 dimensions by
default. `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` must match the configured index.
The provider boundary can support a future adapter; only OpenAI is implemented.
Up to 32 texts are sent per request, with a conservative 6000 UTF-8 byte limit per
text. Oversized records are reported, not silently truncated. CLI usage reports
input tokens for the batch; they are not falsely attributed to individual chunks.

A unique B-tree index covers museum/object/content type/model/dimensions. Repeating
preparation skips unchanged, valid vectors. Changes to identity fields, description,
source references, model, dimensions, or text version trigger new embedding work.
Provider failures preserve previous chunks. Evidence is checked again before saving;
runtime hash validation excludes stale vectors, including a concurrent edit between
that check and the write. Rerun preparation after imports. No scheduled jobs or
transactions are introduced.

## Atlas index

```bash
python -m app.modules.retrieval index --create
python -m app.modules.retrieval index
```

The first command creates the configured `VECTOR_INDEX_NAME` (default
`visitadvisor_identity_v1`) if absent. Creation is asynchronous: `building` is not
`ready`. Existing incompatible definitions produce a safe configuration error;
they are never automatically replaced or deleted. After changing vector dimensions,
use a new index name, create it, and rerun preparation. The previous index/chunks
remain available for an explicit later cleanup.

The cosine vector index has filter fields for location, embedding model,
dimensions, text version, and content type. Runtime `$vectorSearch` applies all these
filters **before** candidate selection. It fetches up to 20 neighbors using 400 ANN
candidates. Results are additionally checked against current museum objects and
identity hashes. Missing, deleted, moved, or stale objects cannot be returned from
old vectors. Atlas search indexing is eventually consistent; newly prepared records
may not be searchable immediately.

Ordinary B-tree indexes are initialized at API startup. Atlas search-index creation
is an explicit CLI operation and does not prevent the existing API from starting.

## API contract

`POST /api/v1/locations/{location_id}/search`

```json
{"text": "Misty Sea Jan Toorop 1899", "mode": "hybrid", "limit": 10}
```

`POST /api/v1/recognition/match`

```json
{"location_id": "6a9dd4106d52c3bc3965869d", "text": "SK C 1726", "mode": "hybrid"}
```

Modes are `lexical`, `semantic`, and `hybrid` (default). Queries require 1–2000
characters containing letters or numbers. A museum is mandatory and must exist.
Matching returns at most three candidates; general search permits 1–10.

Responses include `location_id`, `mode_used`, `warnings`, and candidates containing
object ID/name/creator/year, source IDs, content availability, scores, and ranking
signals. Vectors, credentials, raw provider errors, and generated stories are not
included. Fetch Phase 3 content through the existing object experience endpoint;
`content_available` means content is stored, not that it has passed factual review.

Matching also returns `status` (`matched`, `ambiguous`, or `no_match`) and a nullable
`matched_object_id`. Phase 5 can use this contract without changing the matcher.

## Ranking and abstention

For the 100–500 object MVP, lexical ranking scans projected identity metadata only
within the selected museum. It normalizes Unicode, accents, case, punctuation, and
whitespace, then scores accession boundaries, title/alias phrases, fuzzy title
windows, creator tokens, and date phrases. It never scans globally. More than 500
objects causes an explicit error instead of silently omitting objects.

The initial heuristic assigns exact accession 1.0, multiword title/alias phrase
0.86, and single-word title 0.65. Partial titles are weaker; creator/date evidence
can add support. A unique strong identity with a margin of at least 0.15 can bypass
semantic work in hybrid mode (`mode_used=lexical_exact`), avoiding an API call.

For other hybrid queries, Atlas cosine scores are converted back to nonnegative
cosine similarity. Ranking takes the maximum of lexical score, 0.65 × similarity,
and 0.7 × lexical + 0.3 × similarity. Semantic-only mode ranks by similarity.
Low-scoring candidates are omitted (0.25 for hybrid/lexical, 0.3 for semantic).

Automatic identification requires strong exact identity evidence and a 0.15 lead.
Semantic similarity alone never automatically identifies an object. Near ties
remain ambiguous; an empty result is `no_match`. These scores and thresholds are
**heuristics, not confidence probabilities**, and require calibration against real
OCR from Phase 5. Synthetic label tests are not a field accuracy measurement.

## Availability and cost

Lexical mode makes no provider calls. Hybrid mode explicitly falls back to lexical
results on embedding/index failures and returns a safe warning code. Semantic-only
mode returns 503 instead of silently pretending to perform vector search. No query
embedding is requested when the index is not ready or no configured embeddings
exist. Partial embedding coverage is reported; stale results are excluded.

Query embeddings use a bounded timeout with no automatic retry. Offline preparation
retries transient failures up to three attempts and stops paid work on exhausted
credits or provider failures. Saved object vectors are reused; there is no query
embedding cache, so repeated non-exact semantic queries each use the provider.
There is no generative-model call during matching. Public rate limiting and
production exposure remain outside this local development phase.

## Implementation references

- [OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)
- [PyMongo async index management](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/)
- [MongoDB Vector Search query filters](https://www.mongodb.com/docs/vector-search/query/aggregation-stages/vector-search-stage/)

## Verification — 2026-09-09

- 129 automated tests pass; Ruff lint/format and mypy pass.
- Real Atlas contains 103 identity chunks for 103 objects (100 imported and three
  manual records). All hashes match current canonical metadata; index is ready.
- Initial embedding preparation consumed 16,751 input tokens in total. Repeating
  preparation reports 0 generated, 103 skipped, 0 failed, and 0 input tokens.
- Live API checks below returned HTTP 200 with no fallback warnings. Each positive
  query ranked the expected object first. These six controlled queries demonstrate
  integration behavior, not production recognition accuracy.

| Query case | Outcome | Retrieval path |
| --- | --- | --- |
| `SK C 1726` | Misty Sea matched | Exact lexical; no embedding call |
| `Misty Sea Jan Toorop 1899` | Misty Sea matched | Exact lexical; no embedding call |
| `Mlsty Sea Jan Toorop 1899` | Misty Sea first candidate, ambiguous | Hybrid |
| Seascape made from tiny dots of unmixed pigment | Misty Sea first candidate, ambiguous | Semantic |
| Six monkeys in a cage, a mother with two young | Heimwee first candidate, ambiguous | Hybrid; English query, Dutch description |
| Unrelated computer warranty query | No match | Hybrid |

The final local smoke run observed 0.46–3.23 seconds per request,
including remote database/provider latency. This is a small sample, not a latency
SLA. The API runs on port 8001. Phase 3's unreviewed-content issue remains open;
Phase 4 does not resolve or hide it.
