# Phase 5 expanded validation — 2026-09-16

The expanded test ran against real Atlas and the actual FastAPI image endpoint.
Phase 5 **does not pass**: building-detail recognition, positive-photo coverage,
and automatic-match sample size remain insufficient. Automatic visual API
identification remains disabled. Phone/network timing is still unmeasured.

The [v3 follow-up](12-visual-expanded-validation-v3.md) expands architectural
references and fixes the two building-detail misses, while exposing false simulated
automatic selections on a broader dataset. Use v3 for the current state; this
document remains the frozen v2 result.

## References and download fix

Added five objects from their exact official Rijksmuseum accession records:
The Battle of Waterloo (`SK-A-1115`), The Merry Family (`SK-C-229`), The Syndics
(`SK-C-6`), Shiva Nataraja (`AK-MAK-187`), and The Threatened Swan (`SK-A-4`).
The existing manual Night Watch, Milkmaid and Jewish Bride identities were not
duplicated or merged. Added the Rijksmuseum building as a separate building object
with a Commons source record and an explicitly attributed facade reference.

All six new objects have prepared embeddings. Atlas now contains 109 objects,
107 sources, 91 active reference sets and 455 visual vectors. The previous 85
reference set IDs are preserved. New text embeddings and generated visitor stories
were not prepared; this work exercises visual recognition only.

The initial building preparation failed because Wikimedia rejected the generic
HTTP client user-agent. `ReferenceClient` now identifies VisitAdvisor on every
request, including redirects, while retaining host, byte, timeout and request
bounds. The same download then succeeded. A regression test covers a redirect to
a server that rejects unidentified requests. No host allowlist was broadened.

The five official-image preparations reported `REFERENCE_RIGHTS_UNKNOWN`; their
source evidence remains recorded, but rights must be reviewed before production
reuse. The building reference records Danny William Wilson and CC BY-SA 4.0.
Reference preparation stores descriptors and evidence only. The temporary local
building-reference inspection copy was removed. Test photographs remain in the
ignored local evaluation folder; no image pixels are committed or stored in Atlas.

The [reference preparation audit](visual-validation-v2-reference-preparation.json)
retains import records, object/source IDs, source evidence, the original failure,
and successful preparation reports. This was a bounded manual validation exercise;
automatic reference collection after location selection remains deferred.

## Frozen dataset

The new manifest contains **62 photos**: 14 calibration and 48 evaluation. It adds
26 independently photographed queries, with assistant visual inspection and
source/EXIF review before inference. No reference reproduction, generated image,
or synthetic crop was admitted as independent query evidence. Human identity and
capture-independence review remains necessary for field sign-off.

The former manifest remains unchanged. Ten former building negatives were excluded
from this new manifest because the building now has references. No old photo was
relabeled or moved between calibration and evaluation. JoJan's 2013 session stays
in calibration. Related public-photo sessions stay together; the two occluded Swan
photos count as only one partial capture group. The building reference photo never
appears in the query manifest.

Calibration has 12 positives across eight identities and two negatives. Two
negatives are still too few to represent rejection behavior reliably.
Evaluation has 18 positives across ten identities: 13 full views and five partial
views. It includes paintings, sculpture and a building, plus 30 negatives spanning
all four required categories. Eight wrong-location queries depict the Louvre or
other sites while selecting the Rijksmuseum. These are image rejection checks,
not proof of physical geolocation or a populated second-location search index.

Artifacts: [manifest](visual-validation-v2-manifest.json),
[credits and download hashes](visual-validation-v2-credits.json),
[dataset changes](visual-validation-v2-dataset-changes.json), and
[coverage audit](visual-validation-v2-audit.json).
The corpus contains previously inspected regression photos; its overall metrics
are not a fresh blind field-test estimate.

## Calibration and evaluation results

Thresholds were fitted on calibration photos and frozen before evaluation. The
[calibration artifact](visual-validation-v2-calibration.json) binds model,
preprocessing, manifest, raw/normalized photo hashes and reference set IDs.
Thresholds were not adjusted after observing evaluation results.

| Baseline | Full top 3 | Partial top 3 | Simulated automatic correct/selected | Negative automatic errors |
|---|---:|---:|---:|---:|
| Global embeddings | 13/13 | 3/5 | 11/11 | 0/30 |
| Regional embeddings | 13/13 | 3/5 | 13/13 | 0/30 |
| Local features | 12/13 | 3/5 | 15/15 | 0/30 |

The regional policy selects 13/18 positive photos (72.2%) in offline simulation.
This passes the coverage target, but 13 automatic selections cannot meet the
required minimum of 30. Observed precision on this tiny sample does not establish
98% production precision. The [full report](visual-validation-v2-results.json)
records all rows, thresholds, scores and failed gates. `validate` correctly exits
one and reports `ready_for_regional_api_validation=false`.

Both embedding methods miss the two close photographs of architectural reliefs
on the north facade. Those reliefs are absent from the single central-facade
reference. This is evidence that whole-building reference coverage is insufficient
for these detail queries; it is not a reason to lower confidence thresholds.
The local-feature baseline also misses the independent full building view.
Painting partials and both held-out Shiva views succeed, but they do not establish
general three-dimensional or arbitrary-fragment recognition.

## API regression and latency

All 62 multipart requests returned HTTP 200 through FastAPI and real Atlas.
Calibration positives matched in the top three in 12/12 cases; evaluation positives
matched in 16/18 cases. The same two architectural partials were missed.
Six of the 30 evaluation negatives returned unrelated candidates; 24 returned no
candidates. There were zero actual automatic matches because the API remains
candidate-only. Candidate suggestions are not automatic identification.

Backend p95 was **1.702 seconds** across all 62 requests and **1.521 seconds** across
the 48 evaluation requests. This sequential in-process ASGI measurement includes
upload handling, local CPU inference and Atlas. It excludes startup, camera
capture, phone networking and external HTTP transport. It passes the backend
latency target for this run, not the mobile end-to-end gate.

The [API report](visual-validation-v2-api.json) retains every response and timing.
The [comparison and integrity summary](visual-validation-v2-summary.json) verifies
manifest/photo hashes, frozen reference sets after the run, and the twenty suggested
collection targets' reference coverage. Applying the frozen threshold policy to
saved API scores is an offline simulation only; it does not activate the API.
No OCR, text-embedding or paid model-provider calls were made.

## Remaining work

1. Add independent reference views that actually cover architectural details and
   other intended angles. Preserve this failed run when developing improvements.
2. Collect at least 46 additional positive captures to fill this regression
   corpus's minimum gaps: ten more identities with a full view and two partial
   views, plus sixteen missing partial captures for existing identities.
3. Broaden calibration negatives. Reserve fresh evaluation captures before further
   development; previously inspected photos remain regression data.
4. Recalibrate and validate against the new frozen corpus. Require partial top-three
   accuracy of at least 80%, at least 30 automatic selections at 98% precision,
   all dataset gates, and the other documented acceptance criteria.
5. Validate the accepted regional policy through the API, then measure on the
   physical phone and intended network before enabling automatic identification.

The [field-photo checklist](11-visual-field-photo-checklist.md) provides twenty
concrete object IDs and accessions. It is a collection worksheet, not a declaration
that the objects are currently on display or that the missing photos exist.
The user must supply or arrange real field captures and phone/network measurements;
this environment cannot perform those physical actions.

## Reproduction

Run the manifest beside the local photos; the copy under `docs` is an audit artifact.
Use new output paths because calibration/validation refuse overwrites.

```bash
OPENAI_API_KEY='' .venv/bin/python -m app.modules.visual audit \
  --manifest .local/visual-public-photos/validation-manifest-v2.json \
  --output audit-v2-rerun.json

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENAI_API_KEY='' \
  .venv/bin/python -m app.modules.visual validate \
  --manifest .local/visual-public-photos/validation-manifest-v2.json \
  --calibration .local/visual-public-photos/validation-v2/calibration.json \
  --output validation-v2-rerun.json

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  .venv/bin/python -m scripts.visual_api_smoke \
  --manifest .local/visual-public-photos/validation-manifest-v2.json \
  --output api-v2-rerun.json
```

Run offline loading separately from the API benchmark to avoid distorting latency.
Verification: **228 tests passed, one opt-in live fixture skipped**; Ruff lint,
Ruff formatting and `mypy app` passed. The real Atlas preparation, evaluation and
62-request API run above were executed separately from those unit tests.
