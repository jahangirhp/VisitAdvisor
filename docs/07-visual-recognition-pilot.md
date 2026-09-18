# Phase 5 — Visual object recognition pilot

**Updated:** 2026-09-18
**Status:** Preparation, benchmark tools, and opt-in visual candidate API implemented.
Independent recognition accuracy and automatic identification are not complete.

The latest [v5 validation](15-visual-online-validation-v5.md) passes every dataset
and API transport gate on a 180-photo source-separated corpus. Recognition still
fails the required retrieval, automatic-precision, and negative-rejection gates,
so candidate-only behavior remains the correct production setting.

## Scope and current behavior

Recognize objects and places without readable labels using image embeddings, with
user-selected visit location as a mandatory candidate filter. Paintings, statues,
artifacts, rocks, architectural details, and buildings can all use the same
reference model. Multiple source-linked views may represent one identity. A
building is an object within the selected visit location. The long-term target
is almost any observable object; the MVP only covers known objects with references.
The current Rijksmuseum importer supplies paintings, so other categories require
explicit reference manifests and separately sourced object records.

OCR remains a separate recognition path. `mode=visual` never invokes OCR or a
text-embedding provider. No identity is inferred from a model's remembered title.
The initial visual API returns candidates only, with `VISUAL_UNCALIBRATED`.
Automatic selection must wait for independent validation. OCR's existing matched
responses are unchanged. Camera UI, continuous video, custom training, and global
recognition are outside this pilot.

## Reference preparation and storage

The explicit CLI processes at most 500 selected-location objects, four reference
views per object, and 32 remote requests per object, including redirects. Museum
resolution follows `shows` → VisualItem → `digitally_shown_by` → DigitalObject →
`access_point`. It also retains the IIIF manifest as reuse/attribution evidence.
All relevant branches are visited until the bounds are reached; truncation,
missing references, unsupported image services, unknown rights, and failures are
reported. A request-limit failure does not replace a working reference set.

Only HTTPS on `data.rijksmuseum.nl`, `id.rijksmuseum.nl`, `iiif.micr.io`, and
`upload.wikimedia.org` is accepted. Every redirect is checked. URLs with credentials
or nonstandard ports are rejected. Downloads have a 35-second overall timeout,
15-second network timeout, 1 MiB metadata bound, and 5 MiB image bound. Existing
image validation enforces actual format, 16 megapixels, EXIF orientation, and a
metadata-free normalized JPEG. The museum image URL is bounded to 800 pixels wide.
There is no retry loop or scheduler; rerun the CLI to retry failed records.

Images exist only in memory during preparation. **No reference-image cache or
permanent image archive is created.** Model weights are cached locally. Atlas stores
source-linked metadata/evidence and separate derived descriptors, not pixels.
Changing the model requires retrieving references again. Downloaded bytes and the
normalized parent image are identified by hashes; region coordinates refer to the
normalized parent. Image hashes alone are not source evidence.

`visual_references` stores one active set per object/location. Its `reference`
metadata includes source IDs, image/object/visual/digital URIs where available,
original linked evidence, rights status, and an operator-supplied viewpoint for
manual references. `derived` stores full-image/four overlapping regional vectors,
normalized dimensions, and up to 800 SIFT feature points/descriptors per view.
Each set records a canonical evidence hash, model revision, preprocessing and
package versions, fetch time, and a unique set ID. Metadata plus descriptors are
capped at 12 MiB per object set.

`visual_vectors` is the separate Atlas vector-search projection. The CLI stages a
complete new set before atomically publishing it using a compare-and-swap guard.
Missing/moved objects, source changes, model changes, and old reference sets are
excluded from searches. A failed refresh preserves the previous set, which remains
eligible only while its canonical evidence is current. Unchanged references skip
inference. Fetch timestamps are refreshed. Previous vectors are removed on a
best-effort basis after publication; failed or interrupted staging can leave
ineligible vectors for later maintenance. Text chunks and their index are untouched.

## Baselines and compute

The experiment uses local `facebook/dinov2-small` at pinned revision
`ed25f3a31f01632728cabb09d1542f84ab7b0056`. Its normalized 384-dimensional CLS vector
represents each image/region. Reference and query photos use the same processor
and weights. Five reference regions comprise the full view and four overlapping
65% corner regions. Dependency versions are included in the preprocessing identity
so upgraded preprocessing cannot silently mix with stored descriptors.

The benchmark compares:

1. Whole-image DINOv2 embeddings.
2. Whole-image plus regional DINOv2 embeddings.
3. SIFT local features, a 0.7 descriptor ratio test, and planar RANSAC homography.

These are experimental baselines, not a claim of universal accuracy. Homography
verification is useful for planar surfaces, and does not establish arbitrary 3D
viewpoint support. Additional views provide reference coverage for sculptures and
buildings. Similar-looking objects or featureless fragments may remain ambiguous.
No model is trained. Inference runs on CPU with two Torch threads; there is no
per-scan model-provider charge. Download size, installed dependencies, inference
memory, and latency still matter; CPU-only Torch avoids unnecessary GPU packages.

References: [DINOv2 model card](https://github.com/facebookresearch/dinov2/blob/main/MODEL_CARD.md)
(Apache 2.0), [Transformers DINOv2](https://huggingface.co/docs/transformers/model_doc/dinov2),
and [OpenCV feature matching and homography](https://docs.opencv.org/4.x/d1/de0/tutorial_py_feature_homography.html).
Model licensing is separate from the rights on museum images.

## Commands

Install optional dependencies and explicitly download the pinned model:

```bash
.venv/bin/pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -e '.[dev,visual]'
.venv/bin/python -m app.modules.visual model
```

`model` prints the exact model/preprocessing identities needed by benchmark policy
files. Later commands and API startup load cached weights only; they never
silently download a model. No OpenAI key is needed for visual processing.

Prepare a small subset of existing imports (the first three records in the current
sample are manual records without museum image links):

```bash
.venv/bin/python -m app.modules.visual prepare \
  --location-id 6a9dd4106d52c3bc3965869d --offset 3 --limit 25
.venv/bin/python -m app.modules.visual index --create
.venv/bin/python -m app.modules.visual index
```

Reports contain attempted/prepared/skipped/failed/uncovered counts, object IDs,
view counts, and safe warning/error codes. Exit code 1 indicates a failure and
2 invalid arguments. Uncovered objects are explicitly reported, not invented.
Repeat preparation to verify unchanged descriptors are reused. The search index
must report `ready` before API visual matching. Search-index creation is explicit
and existing incompatible indexes are never overwritten.

After upgrading an earlier pilot, rerun `python -m app.modules.visual index` once.
Index setup also fills missing `vector_count` fields from stored descriptors and
adds `canonical_evidence` copies to legacy reference sets whose SHA-256 evidence
hash still matches current sources. This idempotent upgrade handles at most 500
legacy sets per invocation. It does not download images, regenerate embeddings,
change reference set IDs, or edit canonical objects/sources. Stale, invalid, or
oversized sets remain ineligible and require preparation. Runtime does not perform
this upgrade.

For other object types or viewpoints, use `--manifest references.json`. This file
contains an array of explicit references for existing selected objects:

```json
[
  {
    "object_id": "REPLACE_WITH_EXISTING_OBJECT_ID",
    "source_ids": ["REPLACE_WITH_EXISTING_SOURCE_ID"],
    "image_url": "https://upload.wikimedia.org/REPLACE_WITH_ACTUAL_IMAGE_PATH",
    "viewpoint": "building facade, west side",
    "rights_status": "unknown",
    "evidence": {"source_page": "REPLACE_WITH_SOURCE_PAGE", "note": "Record attribution and reuse evidence here"}
  }
]
```

The CLI checks object/location/source membership and host/size bounds. Manual
image-to-object associations are operator assertions that need review; linking a
source ID does not prove the image shows that object. Multiple views use repeated
object IDs with distinct image URLs, up to four. The manifest replaces the complete
reference set for each supplied object, and must include all views to retain.
Objects without manifest entries are reported uncovered and are not changed.

## Evaluation runner

The [calibration workflow](09-visual-calibration-and-validation.md) now provides
`audit`, `calibrate`, and `validate` commands, frozen artifacts, and detailed
coverage reports. Use it to fit thresholds and perform verified held-out evaluation.
The `benchmark` command below remains an exploratory baseline comparison.

Create a JSON array describing independent phone photographs. Paths are relative
to the manifest directory; paths escaping that directory are rejected. Each row
has `id`, `path`, `location_id`, `expected_object_id` (null for negatives),
`capture_group`, `split` (`calibration` or `evaluation`), `provenance`, `independent`,
`category`, `object_type`, `viewpoint`, and optional `visible_fraction`.

Categories are `full`, `partial`, `outside_reference`, `similar_object`,
`wrong_location`, and `uninformative`. Every derivative of one capture must use
its original capture group. Calibration/evaluation capture groups and exact image
hashes may not overlap. A reference image cannot count as an independent photo.
Positive objects must have active reference coverage in the queried location.
The manifest is bounded to 3,000 photos and five existing locations. It should
contain both splits so leakage checks cover both.
No user evaluation photos are uploaded to Atlas or written by the runner.

The policy JSON contains `model`, `preprocessing` (exact output from `model`), and
`methods`, with `global`, `regional`, and `local` each containing numerical
`minimum`, `automatic`, and `margin` thresholds. Determine thresholds on calibration
photos, freeze the policy, then run held-out evaluation. Scores are not probabilities.
The runner records policy/manifest hashes and active reference-set IDs, reports
all numerators/denominators, abstention, false matches, and results by object type.

```bash
.venv/bin/python -m app.modules.visual benchmark \
  --manifest photos.json --policy policy.json --split calibration > calibration.json
.venv/bin/python -m app.modules.visual benchmark \
  --manifest photos.json --policy policy.json --split evaluation > evaluation.json
```

This compares exact in-memory descriptors against all active references in each
selected location; Atlas vector search is tested separately through the API.
The offline descriptor read uses four-document cursor batches and a two-minute
timeout because local features are much larger than image vectors. Normal API
database operations retain their five-second timeout.
Offline timing excludes HTTP and Atlas and is not an end-to-end latency claim.
Synthetic/reference-derived photos can test the pipeline but cannot satisfy the
independent-photo gate. An evaluation report does not enable automatic API matching.

## Experimental API contract

Set `VISUAL_ENABLED=true` in the ignored `.env` file and restart the API after model
setup and reference preparation. Keep the default false when running OCR only.

```bash
curl -X POST http://localhost:8001/api/v1/recognition/image \
  -F 'location_id=6a9dd4106d52c3bc3965869d' \
  -F 'mode=visual' \
  -F 'image=@/path/to/object.jpg;type=image/jpeg'
```

The default `mode=ocr` retains its existing contract. Visual responses have
`version=visual-pilot-v1`, `method=visual_embeddings`, null `ocr` and `object`,
`covered_objects`, `total_objects`, warnings, and at most three distinct candidates
with object ID/name/type, similarity, source IDs, and content-readiness warnings.
Status is `ambiguous`, `no_match`, or `unreadable`; **never `matched`** at this stage.
A featureless image can be unreadable even though its file is valid.

Atlas search filters selected location, model, preprocessing, dimensions, and active
reference-set IDs before nearest-neighbor selection. It uses exact vector search
over the bounded pilot (at most 500 objects × four views × five regions), aggregates
scores per identity, and verifies every active set's indexed vector count before
returning three distinct candidates. Incomplete indexing returns 503
`VISUAL_INDEX_UPDATING`; a concurrent reference refresh requests a rescan. The provisional 0.45
cosine retrieval floor is not an automatic-identification threshold. Local-feature
matching is an offline benchmark baseline, not part of the API ranking yet.

`VISUAL_UNCALIBRATED` and `SELECT_CANDIDATE_OR_RESCAN` remain explicit. Missing/partial
coverage is reported. A candidate can be selected using existing object/experience
GET endpoints, preserving citations and `CONTENT_UNREVIEWED` or
`CONTENT_NOT_PREPARED`. Visual service/model/index failures return safe 503 codes;
there is no silent OCR fallback. Concurrent inference returns `VISUAL_BUSY`.
No original reference download or content generation occurs during a scan.

Runtime evidence validation joins current objects, sources, and reference sets
inside MongoDB. It compares current evidence against the reference set's
`canonical_evidence` copy using BSON document equality, requiring the same object,
location, external ID, sorted source IDs, source URLs, and nested raw records.
Reference preparation verifies the SHA-256 evidence hash before publishing this
copy. Sources remain canonical; the copy is a freshness guard, not new evidence or
AI-generated content. No image pixels are duplicated.

Only current object summaries and validated reference metadata leave MongoDB;
raw sources, evidence copies, generated stories, and offline descriptors are not
transferred during a scan. The bounded cursor requests up to 501 rows per batch.
The initial evidence read and index-readiness check run concurrently, with inference
behind both gates. Evidence is read again after vector search without caching.
Missing/moved objects, source edits, empty reference views, changed reference sets,
and incomplete indexed vector counts continue to invalidate stale candidates.
BSON equality is conservative about document key order: reordering source keys
can require reference preparation even if their SHA-256 evidence hash is unchanged.
Preparation refreshes the copy when it reuses unchanged descriptors.
Runtime explicitly uses simple binary collation for case-sensitive strings;
equivalent numeric representations follow MongoDB's value comparison semantics.
See [MongoDB BSON comparison rules](https://www.mongodb.com/docs/manual/reference/bson-type-comparison-order/).

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

## Evaluation and acceptance gates

Maintain a manifest with expected object ID (or unknown), museum, original-photo
group, image provenance, viewpoint, and full/partial category. Keep calibration
and held-out evaluation photos separate by original capture; all derivatives of
one capture stay together. Freeze thresholds before running the held-out set.
Exact and normalized duplicate files are rejected, including within one split.

Use at least 20 indexed objects with independent visitor-style photos: one full
view and two partial views per object, with angles, glare, framing and lighting
variation represented. Include at least 30 negative photos spanning objects outside
the reference set, visually similar objects, wrong-museum queries and uninformative
details. Keep negative categories visible in the report.
Full and partial captures must use distinct capture groups for each object. Positive
coverage must include `painting`, `sculpture`, and `building` annotations. Passing
the recognition gates also requires a verified calibration artifact, not only a
handwritten policy or the presence of a calibration split.

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

## Verification — 2026-09-13

- The first live preparation selected 25 existing imports: 22 prepared, three
  without usable image references, zero failed. It created 110 visual vectors.
- A repeat three-object sample reported zero prepared, two skipped, one uncovered,
  and zero failed. The two prepared objects reused their descriptors.
- Initial storage for 22 references was approximately 24.3 MB of BSON reference
  records (including local features/evidence) plus 0.58 MB in the vector projection.
  These sizes exclude Atlas index/storage overhead and are not a scaling estimate.
- `python -m scripts.visual_smoke --location-id ID` ran a reproducible,
  reference-derived experiment: three paintings, each with a full image, central
  crop, and perspective transform, plus three procedural negative images. All
  three methods placed the expected object in the top three for 9/9 positives.
  None automatically selected an object for the 3/3 negatives under the smoke
  policy. All independent-photo acceptance gates remained unsatisfied.
- Offline p95 encoding/comparison times for that 12-query sample were approximately
  0.14 seconds for whole/regional embeddings and 0.23 seconds for SIFT. Peak process
  RSS was approximately 992 MiB. These measurements include local CPU inference,
  not HTTP or Atlas search. Temporary synthetic photos were removed after the run.
- Live FastAPI upload checks with the real Atlas index returned the expected
  object among candidates for a full view (3.48 seconds) and a central partial view
  (3.08 seconds). A blank image returned `unreadable` (1.88 seconds). Responses did
  not invoke OCR or automatically identify an object. Three checks are not a p95
  latency result or independent accuracy evaluation.
- A refresh/indexing race discovered during API checks was fixed: search now
  verifies indexed coverage for every active reference set and requests a retry
  while Atlas catches up. The final full/partial checks passed with that guard.
- The full inventory subsequently processed all 100 existing imports: 85 objects
  have references (85 views and 425 stored vectors); 15 have no usable image links.
  One refresh encountered a database error, preserved its previous reference, and
  succeeded on retry without repeating descriptor inference. Canonical object and
  source counts remain 103 and 101 respectively.
- The [full-corpus synthetic report](visual-pilot-smoke-results.json) compares the
  same 12 queries against all 85 referenced objects. Each method retained 9/9
  positives in the top three and zero automatic matches on 3/3 negatives. Offline
  p95 was 0.14 seconds for embeddings and 0.52 seconds for SIFT; peak process RSS
  was approximately 1,385 MiB. These are engineering results, not field accuracy.
- With all 85 objects available and the OpenAI key absent, final live full/partial
  requests returned the expected candidate in 4.35/4.01 seconds; blank input was
  `unreadable` in 1.91 seconds. Concurrent offline bulk loading caused one earlier
  API database timeout, returned as a safe 503. Load/latency validation remains open.
- Final local checks: 207 tests pass, Ruff lint/format pass, and mypy passes across
  65 application files. Two existing TestClient dependency deprecations remain.

## Completion status

The subsequent [public-photo validation](08-visual-public-photo-validation.md)
tested 45 photos through FastAPI and real Atlas: 13/13 positives in the top three
(5/5 held-out), 27/31 unindexed scenes returning no match, four unindexed scenes
returning unrelated candidates, and one two-target scene remaining ambiguous.
Original API p95 was 5.70 seconds, above the target. The latency fix subsequently
reduced backend p95 to 2.05 and 1.74 seconds in two runs on the same 45-photo corpus,
preserving every candidate and score. This passes the measured backend latency target; it does not
complete the dataset, automatic-identification, or phone/network validation gates.

Implemented: bounded reference resolution/manual manifests, temporary image
processing, versioned image/regional/SIFT descriptors in Atlas, atomic reference
publication, a separate visual index, a capture-separated benchmark runner, and
opt-in visual candidate responses without OCR.

The [v5 online validation](15-visual-online-validation-v5.md) now supplies a
source-separated 180-photo corpus, 20 held-out identities, complete full/partial
coverage, and 30 held-out negatives. The regional ensemble reaches the automatic
coverage target, but fails full/partial retrieval, automatic precision, and
negative rejection. Improve references and ranking, then freeze another unseen
validation corpus before enabling automatic matching. Physical phone/network
latency validation remains required after recognition gates pass. The existing
Phase 3 factual-content review is still open independently.

The [calibration/validation workflow](09-visual-calibration-and-validation.md) is
implemented and tested. Its first real regression run preserved 5/5 positive
top-three matches and had no simulated automatic errors, but the sample remains
too small and lacks the required types/partial views. Calibration tooling is ready;
representative calibration and full pilot validation are still required.
