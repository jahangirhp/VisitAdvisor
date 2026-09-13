# VisitAdvisor — MVP Data Model

**Version:** 0.1  
**Database:** MongoDB Atlas

## Phase 1 implementation contract

The examples below describe the broader MVP. For the implemented Phase 1 API,
the following decisions supersede conflicting examples:

- Only `locations`, `objects`, and `sources` exist; `knowledge_chunks` is deferred.
- API and stored fields use snake_case. MongoDB `_id` is an ObjectId; API `id`
  and stored relationship IDs are its lowercase 24-character hex string.
- Request models exclude IDs and timestamps; domain models also serve as API
  response models. Repositories explicitly convert `_id` at the boundary.
- Timestamps are server-generated UTC BSON dates, serialized as ISO timestamps.
- Locations require name, slug, type, city, and country. Slugs are trimmed and
  lowercased, then validated as hyphen-separated ASCII letters/digits. Status is
  `active` (default) or `inactive`; coordinates use `latitude` and `longitude`.
- Location types: museum, historical_site, landmark, gallery, exhibition,
  public_space, attraction, other.
- Objects require name, free-form type, and a valid parent location. Optional
  creator/year/description/story/history, aliases/facts/source_ids lists, and
  JSON metadata support different physical entities. Year accepts integer or
  text. Source IDs are deduplicated and must all exist in the same location.
- Sources require title, constrained type, and a valid parent location. URL is
  optional (HTTP/HTTPS only); publisher, external_id, and language are optional.
  Types: official_api, official_website, public_dataset, wikipedia, wikidata,
  document, manual, other. Source references are evidence pointers; no source
  content is fetched or generated during this phase.
- Startup creates unique `locations.slug`, `objects.location_id`, and
  `sources.location_id` indexes idempotently. Additional suggested indexes below
  are deferred until their query paths exist.
- No delete/update routes exist, so parent/source relationship checks need no
  deletion coordination yet. Future deletion must preserve these relationships.

## Phase 2 ingestion additions

Phase 2 implements a bounded Rijksmuseum CLI importer with the existing three
collections. Imported objects and sources add read-only `import_provider` and
`external_id` fields. The external ID is the full official persistent URI, not
a title or a locally assigned accession number. Both collections have a partial
unique `(location_id, import_provider, external_id)` index that applies when the
provider and external ID are strings. Manual API records are not affected.

Imported sources retain `raw_record` (latest Linked Art JSON) and
`last_fetched_at` (UTC BSON date). Each object links to its evidence source via
`source_ids`. The object stores a private `import_hash` of normalized import
values to skip unchanged updates; this is excluded from API responses.
Accession number and materials are stored under `metadata.rijksmuseum`.

Reimports update only nonempty source-mapped fields, merge aliases/source IDs
and museum metadata, and preserve facts/story/history and unrelated metadata.
No title-based merging with manual records occurs. Source evidence is the latest
snapshot only; historical snapshots and scheduled synchronization are deferred.
See README for CLI usage, report semantics, and field mapping behavior.

## Phase 3 prepared content

`objects.prepared_content` is an optional generated subdocument. It contains
introduction, facts, story, history, and interesting_fact with per-section source
citations, plus source_ids, evidence_hash, provider, requested_model, model,
prompt_version, generated_at, input_tokens, output_tokens, and review_status.
Canonical object fields and source records are not overwritten. Existing
collections/indexes suffice. `GET /api/v1/objects/{object_id}/experience` retrieves
this subdocument; absent content returns 404. AI output is always marked unreviewed.

## Phase 4 retrieval additions

`knowledge_chunks` now stores one canonical `identity` text/vector per museum,
object, embedding model, and dimension count, enforced by a unique compound index.
Fields use snake_case and retain source IDs, content hash, text version, origin,
language, embedding provider/model/dimensions, and timestamps. Generated Phase 3
content is not embedded. Canonical object/source records are unchanged. Atlas
Vector Search has a separate explicitly created index with mandatory museum/model/
dimension/version/content-type filters. See `05-retrieval-and-matching.md` for the
implemented schema and query contract; these supersede the illustrative fields below.

## Current Phase 5 OCR response

No new collections or persisted fields are introduced. Label images and OCR text
are transient. Recognition returns the existing object response (including nullable
`prepared_content`) only for a confident museum-scoped match. Unreviewed content
keeps its review status and citations. See `06-image-recognition.md`.

## 1. Goal

The MVP data model must support:

- one or more supported locations
- real objects/places within a location
- source traceability
- prepared visitor content
- text embeddings
- OCR/object matching

Keep the schema flexible and avoid premature normalization.

## 2. Collections

Use only four collections initially:

```text
locations
objects
sources
knowledge_chunks
```

## 3. `locations`

Represents a supported museum, landmark, historical site, exhibition, or other destination.

### Example

```json
{
  "_id": "loc_rijksmuseum",
  "name": "Rijksmuseum",
  "slug": "rijksmuseum",
  "type": "museum",
  "city": "Amsterdam",
  "country": "Netherlands",
  "coordinates": {
    "lat": 52.359998,
    "lng": 4.885219
  },
  "officialWebsite": "https://www.rijksmuseum.nl/",
  "status": "ready",
  "createdAt": "2026-09-06T00:00:00Z",
  "updatedAt": "2026-09-06T00:00:00Z"
}
```

### Required MVP Fields

| Field | Type | Notes |
|---|---|---|
| `_id` | string/ObjectId | Stable ID |
| `name` | string | Display name |
| `slug` | string | Unique human-readable key |
| `type` | string | museum, landmark, site, etc. |
| `city` | string | Optional but recommended |
| `country` | string | Optional but recommended |
| `coordinates` | object | Optional for MVP |
| `officialWebsite` | string | Optional |
| `status` | string | draft/importing/ready/error |
| `createdAt` | date | |
| `updatedAt` | date | |

### Recommended Indexes

```text
unique: slug
index: status
```

## 4. `sources`

Represents an external information source used to build location knowledge.

### Example

```json
{
  "_id": "src_rijks_api",
  "locationId": "loc_rijksmuseum",
  "type": "api",
  "name": "Rijksmuseum Collection API",
  "url": "https://...",
  "publisher": "Rijksmuseum",
  "language": "en",
  "isOfficial": true,
  "externalId": "collection-api",
  "lastFetchedAt": "2026-09-06T00:00:00Z",
  "createdAt": "2026-09-06T00:00:00Z"
}
```

### Required MVP Fields

| Field | Type | Notes |
|---|---|---|
| `_id` | string/ObjectId | |
| `locationId` | reference | Parent location |
| `type` | string | api, website, pdf, dataset |
| `name` | string | Source display name |
| `url` | string | Source URL if applicable |
| `publisher` | string | Optional |
| `isOfficial` | boolean | Useful first-level trust signal |
| `externalId` | string | Optional stable provider ID |
| `lastFetchedAt` | date | |

### Recommended Indexes

```text
index: locationId
unique where possible: locationId + externalId
```

## 5. `objects`

Represents the real-world entity the visitor may recognize.

An object can represent:

- artwork
- artifact
- room/feature
- monument
- building
- historical point
- exhibit
- statue
- other identifiable place/object

### Example

```json
{
  "_id": "obj_night_watch",
  "locationId": "loc_rijksmuseum",
  "externalId": "SK-C-5",
  "type": "artwork",
  "name": "The Night Watch",
  "aliases": [
    "De Nachtwacht",
    "Night Watch"
  ],
  "creator": "Rembrandt van Rijn",
  "year": 1642,
  "zone": "Gallery of Honour",
  "identifiers": [
    "SK-C-5"
  ],
  "description": "...",
  "facts": [
    "...",
    "...",
    "..."
  ],
  "story": "...",
  "history": "...",
  "interestingFact": "...",
  "sourceIds": [
    "src_rijks_api"
  ],
  "searchText": "The Night Watch De Nachtwacht Rembrandt 1642 SK-C-5 ...",
  "createdAt": "2026-09-06T00:00:00Z",
  "updatedAt": "2026-09-06T00:00:00Z"
}
```

### Required MVP Fields

| Field | Type | Notes |
|---|---|---|
| `_id` | string/ObjectId | |
| `locationId` | reference | Mandatory |
| `externalId` | string | Prefer source/venue stable ID |
| `type` | string | Flexible entity category |
| `name` | string | Mandatory |
| `aliases` | string[] | Important for OCR matching |
| `creator` | string | Optional |
| `year` | string/number | Keep flexible initially |
| `zone` | string | Optional room/area |
| `identifiers` | string[] | accession IDs, catalogue IDs, etc. |
| `description` | string | Concise canonical description |
| `facts` | string[] | Prepared facts |
| `story` | string | Short visitor story |
| `history` | string | Deeper historical context |
| `interestingFact` | string | Optional |
| `sourceIds` | reference[] | Source traceability |
| `searchText` | string | Normalized text for matching/indexing |
| `createdAt` | date | |
| `updatedAt` | date | |

### Recommended Indexes

```text
index: locationId
index: locationId + name
unique when available: locationId + externalId
text/search index: name, aliases, creator, identifiers, searchText
```

## 6. `knowledge_chunks`

Stores pieces of searchable knowledge and their vector embeddings.

Keep embeddings separate from the large `objects` document so multiple content types/languages can be embedded independently.

### Example

```json
{
  "_id": "chunk_nightwatch_history_01",
  "locationId": "loc_rijksmuseum",
  "objectId": "obj_night_watch",
  "contentType": "history",
  "language": "en",
  "text": "...",
  "embedding": [0.012, -0.034, 0.098],
  "sourceIds": [
    "src_rijks_api"
  ],
  "embeddingModel": "provider/model-version",
  "createdAt": "2026-09-06T00:00:00Z"
}
```

### Required MVP Fields

| Field | Type | Notes |
|---|---|---|
| `_id` | string/ObjectId | |
| `locationId` | reference | Mandatory filter for retrieval |
| `objectId` | reference | Related object |
| `contentType` | string | description/facts/story/history/search |
| `language` | string | ISO language code |
| `text` | string | Embedded text |
| `embedding` | number[] | Vector |
| `sourceIds` | reference[] | Optional but recommended |
| `embeddingModel` | string | Supports later re-embedding |
| `createdAt` | date | |

### Recommended Indexes

```text
index: locationId
index: objectId
index: locationId + objectId
Atlas Vector Search index: embedding
Vector filter fields: locationId, objectId, language, contentType
```

## 7. Relationships

```text
Location 1 ─── N Source
Location 1 ─── N Object
Object   1 ─── N KnowledgeChunk
Source   N ─── N Object          (via sourceIds)
Source   N ─── N KnowledgeChunk  (via sourceIds)
```

Keep these as ID references rather than creating a graph database.

## 8. OCR Matching Fields

The recognition layer should build/search against normalized values from:

```text
name
aliases[]
creator
year
identifiers[]
zone
searchText
```

For every object, generate a normalized `searchText` such as:

```text
the night watch de nachtwacht rembrandt van rijn rembrandt 1642 sk-c-5 gallery of honour
```

Recommended normalization:

- lowercase
- Unicode normalization
- remove unnecessary punctuation
- retain meaningful numbers
- normalize whitespace
- optionally remove accents for a secondary search representation

## 9. Matching Query Constraint

When the visitor has selected a location, all recognition queries must be constrained by:

```text
locationId = selectedLocationId
```

This is a core architecture rule.

Do not search all objects globally unless no location is known.

## 10. Source Traceability Strategy

For MVP, object-level source traceability is sufficient:

```json
"sourceIds": ["src_1", "src_2"]
```

If testing shows that conflicting claims or regulated/high-trust content requires fact-level provenance, introduce a separate `facts` collection later.

Do not create it prematurely.

## 11. Data Import Rules

1. Prefer stable external IDs from the source.
2. Upsert by `locationId + externalId` when available.
3. Do not create a duplicate object each time an import is rerun.
4. Preserve the original source reference.
5. Record timestamps.
6. Do not overwrite good existing content with empty incoming fields.
7. Keep raw imported values available during development if useful for debugging.

## 12. Future Collections — Not MVP

Only introduce these when concrete requirements appear:

```text
facts
people
events
relationships
zones
raw_documents
processing_jobs
location_packages
user_sessions
saved_items
```

The four-collection model is intentionally small enough to evolve after the first real ingestion test.

## Planned Phase 5 visual references

The visual pilot will add source-linked reference metadata and separate derived
visual descriptors. This is planned, not an existing schema. See
`07-visual-recognition-pilot.md` for fields, invalidation, and storage decisions.
Current text identity vectors must not be reused as image vectors. Canonical
source evidence and generated visitor content remain separate.
