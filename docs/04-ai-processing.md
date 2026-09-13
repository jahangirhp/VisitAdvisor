# VisitAdvisor — AI & Knowledge Processing Specification

**Version:** 0.1  
**Status:** MVP

## Phase 3 implementation

The foreground `python -m app.modules.knowledge` CLI prepares English content for
imported objects using only stored raw source evidence. It uses a replaceable
provider interface, initially the OpenAI Responses API with structured output.
`OPENAI_API_KEY` and `LLM_MODEL` are centralized settings; they are not required
for ordinary API startup. See README for limits, reruns, and sample review.

Generated content is stored in `objects.prepared_content`, separate from canonical
fields and `sources.raw_record`. Every nonempty section/fact includes a source ID
and exact supporting quote. Model/prompt/provider, source IDs, evidence hash,
generation time, and token usage are retained. Null sections/fewer facts are
preferred over unsupported content, overriding suggested minimum lengths below.
All results are marked unreviewed: structural and quote checks do not prove factual
entailment. Refusal, invalid output, failed generation, or detected concurrent
changes leave prior content intact. No AI extraction rewrites canonical fields.

Phase 4 implements text embeddings, Atlas Vector Search, hybrid retrieval, and
OCR-text matching filtered by museum. The revised Phase 5 requires visual matching
without labels, including partial views, with OCR as optional evidence. See
`07-visual-recognition-pilot.md` for the bounded pilot and validation gates.

## Current Phase 5 OCR implementation

`POST /api/v1/recognition/image` accepts a museum ID and a still label image,
validates and normalizes the image, transcribes visible text, and invokes Phase 4
matching. It returns the saved object/content or uncertain candidates. OCR text is
transient and never becomes canonical evidence. Visual matching is planned but
not yet implemented; camera UI and background processing remain deferred.
See `06-image-recognition.md` for the existing OCR contract.

## 1. Purpose

This document defines what AI does in the VisitAdvisor MVP and, equally importantly, what AI does **not** do.

AI is used to transform source material into structured, visitor-ready knowledge and to support semantic retrieval.

The LLM is not the canonical database and should not invent unsupported historical facts.

## 2. MVP AI Responsibilities

AI may perform:

1. structured extraction from source content
2. normalization of titles, names, dates, and descriptions
3. alias generation when justified by source data
4. visitor-friendly summarization
5. key-fact generation
6. short-story generation
7. historical-context generation
8. interesting-fact selection
9. embedding generation through a dedicated embedding model
10. optional ranking support when OCR matching is ambiguous

AI should not be responsible for:

- global source discovery in MVP
- deciding truth solely from model memory
- inventing missing dates/people/events
- silently resolving conflicting facts without evidence
- recognizing every physical object visually
- generating content from model memory when source evidence is unavailable

## 3. End-to-End Processing Flow

```text
Known Source
     ↓
Fetch Source Data
     ↓
Parse / Clean
     ↓
LLM Structured Extraction
     ↓
Validate Required Fields
     ↓
Store Canonical Object
     ↓
LLM Visitor Content Generation
     ↓
Store Prepared Content
     ↓
Create Search Text
     ↓
Chunk Relevant Knowledge
     ↓
Embedding Model
     ↓
Store in knowledge_chunks
     ↓
Atlas Vector Search
```

## 4. Structured Extraction Contract

The extraction call should return structured JSON, not prose.

### Example Input

```text
Title: De Nachtwacht
Maker: Rembrandt van Rijn
Date: 1642
Object number: SK-C-5
Description: ...
```

### Example Output

```json
{
  "externalId": "SK-C-5",
  "type": "artwork",
  "name": "The Night Watch",
  "aliases": [
    "De Nachtwacht",
    "Night Watch"
  ],
  "creator": "Rembrandt van Rijn",
  "year": 1642,
  "identifiers": [
    "SK-C-5"
  ],
  "description": "...",
  "evidence": {
    "sourceId": "src_rijks_api"
  }
}
```

## 5. Extraction Rules

The extraction prompt/schema should enforce:

- return valid JSON matching the application schema
- return `null`/empty values when information is absent
- do not infer unsupported historical facts
- preserve source-specific IDs
- preserve original names as aliases when useful
- normalize only when confidence is high
- keep dates as source-supported values
- do not fabricate quotations

Application-side schema validation must run after every structured AI response.

Invalid output should be rejected or retried; it should not be written directly to canonical records.

## 6. Visitor Content Contract

For each object, prepare a small content set.

### Required MVP Output

```json
{
  "shortDescription": "...",
  "facts": [
    "...",
    "...",
    "..."
  ],
  "story": "...",
  "history": "...",
  "interestingFact": "..."
}
```

### Content Guidelines

#### Short Description

- 1–2 sentences
- immediately identify what the visitor is seeing
- avoid unnecessary detail

#### Facts

- 3–5 concise facts
- each must be supported by imported source material
- prefer concrete details over generic statements

#### Story

- approximately 80–180 words for MVP
- engaging but factual
- explain why the object/place is interesting
- avoid fictionalized dialogue or invented drama

#### History

- approximately 150–350 words for MVP
- provide useful chronological/historical context
- distinguish uncertainty where the source is uncertain

#### Interesting Fact

- optional
- only generate when a genuinely interesting source-supported detail exists
- never force one

## 7. Evidence Rule

The model may use only the supplied source context for canonical generated content.

Conceptually:

```text
SOURCE DATA = evidence
LLM = transformation layer
GENERATED TEXT = presentation
```

Not:

```text
LLM memory = source of truth
```

At minimum, every generated object must retain `sourceIds`.

## 8. Content Generation Prompt Strategy

Use a stable versioned prompt/template.

Store or log:

```text
promptVersion
model/provider
processing timestamp
sourceIds
```

This allows content to be regenerated later when prompts or models improve.

Do not expose internal prompts to the visitor client.

## 9. Embedding Strategy

Use a dedicated text embedding model.

Do not use the conversational LLM response itself as a vector.

### Embed These Content Types

For each object, create chunks for some or all of:

```text
identity/search representation
short description
facts
story
history
```

For the first MVP, avoid aggressive chunking of already-short content.

### Example Search Chunk

```text
The Night Watch. De Nachtwacht. Artwork by Rembrandt van Rijn, 1642. Object number SK-C-5. Gallery of Honour. ...
```

Store:

```json
{
  "locationId": "loc_rijksmuseum",
  "objectId": "obj_night_watch",
  "contentType": "search",
  "language": "en",
  "text": "...",
  "embedding": [],
  "embeddingModel": "provider/model-version"
}
```

## 10. Optional OCR Processing

The MVP does not require an LLM to identify every object.

Existing OCR branch (visual matching is planned separately):

```text
Image
  ↓
OCR
  ↓
Extracted text
  ↓
Normalize text
  ↓
Filter objects by selected locationId
  ↓
Candidate search
  ↓
Rank candidates
  ↓
Return best match or top 3
```

### Matching Signals

Start with deterministic/search-based signals:

- exact/partial title match
- alias match
- identifier/accession number match
- creator match
- year match
- text search score
- semantic/vector similarity

A simple weighted ranker is enough for MVP.

Example conceptual score:

```text
identifier exact match    very high
name/title match          high
alias match               high
creator + year            medium
semantic similarity       supporting signal
zone/location             supporting signal
```

Do not hard-code permanent weights before testing real OCR data.

## 11. Recognition Confidence Behaviour

### High Confidence

Return the identified object directly.

### Medium Confidence

Return the top 2–3 candidates and ask the user to choose visually.

### Low Confidence

Do not invent an answer. Tell the client that the object could not be identified reliably and allow another scan/manual search.

## 12. Model Usage Strategy

Keep the architecture provider-agnostic.

Use:

- a capable but cost-efficient LLM for extraction/content generation
- a dedicated embedding model for vectorization
- OCR/vision capability for image text extraction

Model choice should be configurable through environment/application configuration rather than spread throughout business logic.

Example:

```text
LLM_MODEL=...
EMBEDDING_MODEL=...
OCR_PROVIDER=...
```

## 13. Cost Control

For MVP:

- process location knowledge once, not once per visitor
- avoid sending duplicate source content repeatedly
- cache/store generated content
- generate embeddings only after successful extraction
- use stable hashes/IDs to avoid reprocessing unchanged objects
- batch operations when provider APIs support it

The desired cost profile is:

```text
Higher one-time cost when onboarding a location
                ↓
Very low AI cost for repeated visitor scans
```

## 14. Quality Checks

Before an imported object is considered ready:

- required fields validate
- object has at least one source
- generated content is non-empty where required
- no obviously unsupported claims are introduced
- embedding exists for searchable content

During MVP testing, manually review a sample of generated objects from every import.

## 15. Logging / Debug Data

During development, record:

- source ID
- object external ID
- model/provider
- prompt version
- extraction result status
- schema-validation errors
- token/request cost where available
- embedding generation status
- OCR text
- recognition candidates and scores
- final selected object

This information will be critical for improving recognition and ingestion quality.

## 16. AI Features Deferred Until After MVP

Do not block MVP on:

- autonomous research agents
- automatic source discovery
- multi-agent verification
- graph-RAG
- personalized storytelling
- audio generation
- live web research during a visit
- custom fine-tuned models
- reinforcement learning/ranking models

## 17. Core AI Principle

```text
Collect trustworthy information first.
Use AI to structure and communicate it second.
Use embeddings to retrieve it efficiently.
Never substitute model memory for source evidence.
```
