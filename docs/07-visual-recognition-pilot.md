# Phase 5 — Visual object recognition pilot

**Date:** 2026-09-11  
**Status:** Data audit and implementation plan; visual matching is not implemented.

## Requirement and scope

Recognize an exhibit from its appearance without a readable label, including
partial views containing distinctive details. The selected museum constrains all
candidates. OCR is optional supporting evidence; absence of text must not prevent
visual matching. Return existing prepared content after identification, preserving
citations and review status. Do not generate history or identities from model memory.

This supersedes earlier OCR-first exclusions in the MVP documents. Start with
20–30 paintings from the existing Rijksmuseum import, then evaluate against all
eligible imported paintings as distractors. This pilot does not establish accuracy
for sculptures, unseen sides of objects, or arbitrary tiny fragments. Those require
appropriate reference views and separate evaluation. Camera UI, live video,
training a custom model, and additional infrastructure remain outside this step.

## Read-only image availability audit

The local Atlas data was inspected on 2026-09-11 without changing stored records:

| Check | Result |
| --- | --- |
| Objects | 103 total: 100 Rijksmuseum imports and 3 manual records |
| Imported source snapshots | 100 |
| Snapshots with `shows` links to visual records | 100 of 100 |
| Direct image references discovered in stored snapshots | None; HTML collection links are not image references |
| Existing image preparation | Importer maps object number/materials, but does not resolve visual records or download images |
| Live sample | First 5 sources sorted by external ID; first visual/digital branch inspected |
| Successfully downloaded and decoded sample images | 4 of 5, requested at 800 pixels wide |

| Object persistent URI suffix | Image service ID | Decoded size | Result |
| --- | --- | --- | --- |
| `200100988` | `mPymb` | 800 × 709 | Available |
| `200104600` | — | — | Visual record has no `digitally_shown_by` field |
| `200105887` | `YAxov` | 800 × 580 | Available |
| `200105889` | `PbGmB` | 800 × 394 | Available |
| `200105901` | `JdGJx` | 800 × 1059 | Available |

Object URIs have the prefix `https://id.rijksmuseum.nl/`. The sample is not a
complete image inventory or proof that 80% of the collection is usable. Missing
digital links must be reported, not guessed. No image files were retained by this
audit. No real visitor photographs were evaluated. Simple rights/license fields
were absent from the four sampled DigitalObject records; reuse metadata still
needs to be resolved before curating the reference set.

The museum documents an object → `shows` VisualItem → `digitally_shown_by`
DigitalObject → `access_point` image path. Its image services provide bounded
resizing and crops. Follow recorded identifiers rather than deriving them from
accession numbers. See the [official IIIF tutorial](https://data.rijksmuseum.nl/tutorials/iiif/)
and [Image API](https://data.rijksmuseum.nl/docs/iiif/image).

## Implementation sequence

### 1. Prepare a reproducible reference inventory

Add an explicit, bounded CLI operation for the selected museum, capped at the
existing 500-object MVP limit. Resolve all relevant visual links with per-object
request limits and report truncation, missing images, failed downloads, and unknown
reuse status. Start with the current 100 imports; do not import a new collection
silently. Select 20–30 eligible paintings with visual variety and similar-looking
distractors. Report which objects are outside reference coverage.

Use known museum/IIIF HTTPS hosts, validate each link and redirect, enforce byte,
pixel and timeout limits, and reuse image validation where appropriate. Retain
bounded reference files in a local ignored cache for the pilot; no new storage
service is necessary. Visitor uploads remain transient. Preserve remote metadata
and source provenance separately from normalized reference images and descriptors.

Planned reference metadata belongs in a dedicated `visual_references` collection:
`location_id`, `object_id`, `source_ids`, object/visual/digital persistent URIs,
image URL, rights/attribution evidence or explicit unknown status, fetched timestamp,
source metadata hash, image content hash, dimensions, and preprocessing version.
Keep original linked metadata as evidence. A crop records its parent image hash
and coordinates; it is a derivative, not another canonical source.

Reprocessing unchanged images should skip descriptor work. Model, preprocessing,
image, or source changes invalidate derived data. Missing/moved objects and stale
references must be excluded at query time. Failed refreshes must not destroy the
previous valid reference set. Do not change Phase 4 text chunks or their index.

### 2. Benchmark visual matching before choosing the runtime model

Compare a pretrained image-descriptor baseline with a local-feature matching
baseline suitable for painting details. These are experiments, not a promised
accuracy result or a selected dependency. Check current model documentation,
licensing, compute needs, and costs when choosing the implementation.

Evaluate full references and bounded regional descriptors for partial views.
Rank distinct objects rather than allowing many crops of one object to fill the
candidate list. Test whether geometric verification of shortlisted candidates
reduces false matches. Similar subject matter alone must not establish identity.

Store derived descriptors with reference hash, model/version, dimensions, and
preprocessing version, separate from evidence. For a small offline benchmark,
exact comparison of the selected museum's descriptors is sufficient. If image
embeddings are selected for API integration, use Atlas with a separate visual
index and mandatory museum/model/version filters; do not add a vector database.

Do not substitute generated image captions and text matching for direct visual
matching. Do not identify objects solely through a vision model's remembered title.

### 3. Integrate the successful baseline into the existing API

Extend `POST /api/v1/recognition/image` after benchmark results justify the choice.
Reuse upload limits and museum validation. The visual path must work without OCR.
Retain explicit label OCR mode for existing use, and define the versioned request
and response changes before implementation. Return the recognition method and
coverage warnings; OCR should be nullable or absent when not requested.

Return `matched`, `ambiguous` with up to three distinct candidates, or `no_match`.
Use an unreadable-image outcome for unusable photos, not `no_text` for a readable
object photo. Missing reference coverage must be explicit. Conflicting visual/OCR
evidence requires candidate selection or another scan, not automatic identification.

Only enable automatic visual selection after calibration. Recheck the selected
object's museum before returning saved content. Scores are not probabilities.
Below threshold, ask for a wider or clearer view; never require a label as the only
recovery option. Preserve `CONTENT_UNREVIEWED` and `CONTENT_NOT_PREPARED` behavior.

## Evaluation and acceptance gates

Maintain a manifest with expected object ID (or unknown), museum, original-photo
group, image provenance, viewpoint, and full/partial category. Keep calibration
and held-out evaluation photos separate by original capture; all derivatives of
one capture stay together. Freeze thresholds before running the held-out set.

Use at least 20 indexed objects with independent visitor-style photos: one full
view and two partial views per object, with angles, glare, framing and lighting
variation represented. Include at least 30 negative photos spanning objects outside
the reference set, visually similar objects, wrong-museum queries and uninformative
details. Keep negative categories visible in the report.

Synthetic crops of reference images are useful engineering checks, but cannot
count as independent visitor-photo evidence. Report partial views separately,
including approximate visible object area where it can be annotated. A positive
partial view must contain enough distinctive evidence for a human to verify its
identity; uninformative fragments belong in the abstention set.

Proposed pilot gates, to be measured rather than claimed:

| Metric on held-out photos | Gate |
| --- | --- |
| Correct object in top 3, full views | At least 90% |
| Correct object in top 3, informative partial views | At least 80% |
| Automatic-match precision | At least 98%, with at least 30 automatic matches |
| Automatic-match coverage across positive photos | At least 50% |
| Negative examples automatically misidentified | Zero in the pilot sample |
| End-to-end latency | Target p95 at most 5 seconds; report hardware and conditions |

Always publish numerators/denominators, abstention rate, false matches, and results
by category. Small samples do not prove production precision; failure of a gate
means candidate-only behavior and more investigation. Record preparation time,
storage, runtime memory, and per-scan provider usage/cost if applicable.

Run `pytest`, `ruff check .`, `ruff format --check .`, and `mypy app` for code
changes. Cover museum isolation, stale references, partial-view matching,
negative queries, deduplicated candidates, missing coverage, and fallback behavior.

## Completion status

- Complete: existing documentation/code review, stored-source coverage audit,
  five-object live image sample, and revised Phase 5 scope/plan.
- Next implementation: reproducible reference inventory and visual benchmark.
- Not yet done: full image eligibility audit, descriptor/model selection,
  visual API implementation, or real-photo accuracy measurement.

Phase 5 remains incomplete until the label-free flow is implemented and evaluated.
