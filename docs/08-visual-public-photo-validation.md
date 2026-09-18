# Phase 5 public-photo validation — 2026-09-14

This run tests the existing candidate-only implementation against public photographs
independent of its museum reference images. It does not complete Phase 5.

The [v5 validation](15-visual-online-validation-v5.md) supersedes the live corpus
and current rerun instructions. This historical manifest assumes 85 covered
objects and treats the museum building as unknown; that annotation is no longer
valid after adding building references. Preserve it as historical evidence and use
the v5 artifacts for the current result.

## Dataset and review

The reviewed dataset contains 45 inputs: 13 positive photos covering eight indexed
paintings, 31 unindexed scenes, and one scene showing two indexed portraits without
a specified target. The unindexed scenes include ten building/garden views, fifteen
sculpture views and six Vermeer painting/gallery views. They are multiple views of
a small number of subjects, not 31 different unknown identities.

Eight positive photos are assigned to calibration. The 37 held-out inputs contain
five full-view positives and 32 negative/ambiguous scenes. The prior five-photo
Misty Sea experiment remains in calibration. JoJan's photographs from the same
2013 visit are kept together in calibration; other related sessions also share
capture groups. No image was moved into evaluation after being tested.

Source descriptions, accession numbers/Wikidata links and visual inspection were
used for expected identities. Official museum reproductions, their derivatives,
and one uncertain copy of Girl with a Pearl Earring were excluded from independent
evidence. The two-portrait scene is an abstention test, not a single-target positive.
The held-out positives include a Nokia Lumia 920 photograph of Dancing Couple.

The query location is the owning Rijksmuseum collection. Several photographs were
taken while works were on loan elsewhere; capture venue is recorded separately.
This experiment does not establish physical-location accuracy. The live database
contains only one location, so real cross-location isolation is not validated here.

The [manifest](visual-public-expanded-manifest.json) and
[source/credit records](visual-public-expanded-sources.json) preserve identities,
splits, image hashes, camera metadata where available, source pages and licenses.
The 1280-pixel Wikimedia thumbnails occupy about 19.3 MB locally in the ignored
`.local/visual-public-photos/` folder. They were not added as reference images to
Atlas. Local review contact sheets are inspection aids, not benchmark inputs.

Open `.local/visual-public-photos/gallery.html` to inspect the photos and credits.
The manifest copy in `docs` is an audit artifact; run the copy next to the local
photos so relative paths resolve correctly.

## Frozen-policy offline experiment

The policy was copied unchanged from the previous synthetic smoke experiment,
before running this dataset. It has not been calibrated on the held-out results.
The reference corpus contains 85 covered objects out of 103 canonical objects.

| Baseline | Held-out positive top 3 | Automatic selections | Incorrect automatic selections on 32 negative scenes | Offline p95 |
| --- | --- | --- | --- | --- |
| Whole-image embeddings | 5/5 | 0 | 0/32 | 0.261 s |
| Regional embeddings | 5/5 | 0 | 0/32 | 0.267 s |
| SIFT local features | 5/5 | 3, all correct | 0/32 | 0.888 s |

Whole-image embeddings returned candidates for four negative scenes; regional
embeddings returned candidates for five. Candidate suggestions are distinct from
automatic identification. SIFT is an offline baseline, not the API matching method.
Zero automatic selections by embeddings does not establish automatic precision:
their automatic coverage is zero. Three SIFT selections are too few to pass the
documented minimum of thirty.

The [offline report](visual-public-expanded-evaluation.json) retains every query,
score, numerator/denominator, gate result, reference-set identity and timing.

## Real Atlas API results

All 45 sequential multipart requests returned HTTP 200. All 13 positive inputs
returned the expected object in the top three: 8/8 calibration inputs and 5/5
held-out inputs. The partial-view successes are the two calibration photographs
of Misty Sea already used in the earlier experiment.

Of the 31 unindexed scenes, 27 returned `no_match`; four returned unrelated
candidates. Those four were one Rodin sculptural scene and three Vermeer views.
The two-portrait scene returned `ambiguous`, as expected. Across the complete set,
there were 18 ambiguous responses, 27 no-match responses and zero automatic
matches. Every response had null OCR; the provider key was absent for the run.

API p95 was **5.698 seconds**, above the five-second target; the maximum was
7.724 seconds. There were no failed requests or retries. Timing includes the
in-process upload handling, inference and real Atlas operations, with cached model
weights on local x86_64 CPU. It excludes app startup and phone/network transport.
There was no concurrent offline corpus loading during these measurements.

The [API report](visual-public-expanded-api-results.json) retains each request's
image hash, status, candidates, scores, timing and split. Zero automatic errors
cannot establish automatic precision because the API never automatically matches.
The practical issues exposed in this original run were candidate suggestions on
unrelated scenes and backend latency exceeding the target, in addition to missing
dataset coverage. The latency follow-up below supersedes that timing result.

The complete regression suite passed: 207 tests. Ruff lint/format and `mypy app`
also passed. Two existing TestClient dependency deprecation warnings remain.

## Latency fix — 2026-09-14

Two optimized 45-photo runs passed: the first measured p95 **2.050 seconds** and
maximum **3.559 seconds**; the final run measured p95 **1.742 seconds** and maximum
**2.724 seconds**. All requests returned HTTP 200. Every recorded candidate ID,
score, status, OCR value, and warning matched the original run exactly. Positive
top-three accuracy remained
13/13, including 5/5 held-out positives. Automatic identification remains disabled.
See the [final API report](visual-public-latency-api-results.json) and
[comparison audit](visual-public-latency-comparison.json), which retains earlier
failed attempts, both successful runs, per-photo timings, the profile, and coverage.

The bottleneck was repeatedly transferring complete canonical source records for
freshness checks. A profiled evidence reply was 2,384,225 bytes before the fix and
63,353 bytes afterward. Runtime now compares current evidence with a validated
copy inside MongoDB and returns small object/reference summaries. It still checks
evidence both before and after vector search. The initial evidence and index
checks run concurrently. Full source records and offline descriptors stay in Atlas.
This does not depend on wire compression, which the tested connection did not
negotiate.

Reference metadata was upgraded without downloading images or rerunning the
encoder. All 85 reference set IDs are unchanged, with 425 vectors and the same
103 canonical objects/101 source records. No new reference photos were collected.
Canonical evidence copies contain source metadata, never pixels or generated text.
The [pilot guide](07-visual-recognition-pilot.md) documents storage, upgrade, size
limits, and BSON comparison semantics.

The regression suite now passes 216 tests. The separately enabled live Atlas test
also passes; it uses a disposable database and checks location isolation, nested
source edits, URL/case changes, missing sources, empty views, moved objects,
model/preprocessing isolation, bounded object counts, projections, and repeatable
metadata upgrades. Its temporary database is removed afterward. Ruff lint/format
and `mypy app` pass.

```bash
RUN_VISUAL_ATLAS_CHECKS=1 .venv/bin/pytest -q tests/test_visual_atlas.py
```

These timings include in-process ASGI uploads, local CPU inference and real Atlas.
They exclude startup, camera capture, mobile networking and external HTTP transport.
They satisfy the backend latency target for this corpus, not a production SLA or
the broader Phase 5 accuracy and field-validation gates.

## Reproduction

```bash
OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENAI_API_KEY='' \
  .venv/bin/python -m app.modules.visual benchmark \
  --manifest .local/visual-public-photos/expanded-manifest.json \
  --policy .local/visual-public-photos/policy.json \
  --split evaluation

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  .venv/bin/python -m scripts.visual_api_smoke \
  --manifest .local/visual-public-photos/expanded-manifest.json \
  --output .local/visual-public-photos/expanded-api-results.json
```

The API harness enables visual mode and removes the OpenAI key for its own process.
It exercises multipart uploads, normal application validation and real Atlas
search, sequentially through FastAPI's in-process ASGI client. Startup initializes
the application's existing indexes. It does not change catalog/reference records.
Measurements exclude model startup, a physical phone, the mobile network and
external HTTP transport. Run offline corpus loading separately to avoid adding
database load to API timing measurements.

## Gates still unmet

The dataset has eight positive identities overall, below the required twenty.
The two photographed partial views are in calibration, so there is no new held-out
partial-view accuracy measurement. There are no indexed building/sculpture
positives. Wrong-location database isolation is now checked by live fixtures, but
the photo corpus still lacks real cross-location examples. The model must be tested
against
distinctive partial views, additional types with valid references, and unrelated
capture sessions. A separate calibration set containing negatives is needed before
selecting new automatic thresholds; this held-out set must not be reused to claim
independent accuracy after tuning on it.

Automatic visual API identification remains disabled. Phase 5 remains incomplete.
