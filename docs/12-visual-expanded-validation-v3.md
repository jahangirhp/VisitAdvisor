# Phase 5 expanded validation v3 — 2026-09-16

Phase 5 remains incomplete. The expanded reference set fixed the two architectural
partial-view failures, but the larger held-out set exposed two painting-detail
misses and three false simulated automatic selections. Automatic API identification
remains disabled.

## Changes made

The Rijksmuseum building reference set was expanded from one facade image to four
independently sourced views. The three new views cover the north entrance, central
frieze and another sculptural facade panel; none is one of the two failed query
images. Wikimedia thumbnails are now accepted by the existing strict reference URL
validator. The allowlist still requires HTTPS, exact approved hosts, no credentials,
standard ports and bounded downloads. Tests cover the new host and reject a lookalike
host.

Two official Rijksmuseum records were added and prepared from exact accessions:
The Fall of Man (`SK-A-129`) and View of Itamaracá Island (`SK-A-4271`). Their
reference-resolution reports retain `REFERENCE_RIGHTS_UNKNOWN`, so rights review
is still required before production reuse.

Atlas now contains 111 objects, 109 sources, 93 active reference sets and 480 visual
vectors. Existing canonical evidence remains separate from derived descriptors.
No image pixels were added to Atlas, no generated content was changed, and no OCR,
text-embedding or paid model call was used.

## Dataset and frozen calibration

The v3 manifest extends v2 without modifying its 62 rows. It adds nine positive
evaluation photos for two painting identities and six calibration negatives across
paintings, sculpture, furniture and gallery interiors. Near-duplicate negatives
were excluded. The resulting frozen corpus contains 77 photos:

- calibration: 12 positives and eight negatives;
- evaluation: 27 positives across 12 identities and 30 negatives;
- evaluation viewpoints: 17 full views and ten partial views;
- positive types: painting, sculpture and building;
- negative categories: outside-reference, similar-object, wrong-location and
  uninformative.

Public-photo provenance, download hashes, camera metadata and capture groups are
recorded in the [credits artifact](visual-validation-v3-credits.json). Identity and
independence still require human field confirmation. The corpus includes previously
inspected regression photos and is not a fresh blind field study.

Thresholds were fitted only on calibration photos, then frozen before evaluation.
All 12 calibration positives were selected by the embedding methods, with no false
selection among the eight calibration negatives. Eight negatives remain a small
calibration sample.

## Held-out results

| Baseline | Full top 3 | Partial top 3 | Simulated automatic correct/selected | Negative automatic errors |
|---|---:|---:|---:|---:|
| Global embeddings | 17/17 | 7/10 | 18/19 | 1/30 |
| Regional embeddings | 17/17 | 8/10 | 23/26 | 3/30 |
| Local features | 16/17 | 7/10 | 23/23 | 0/30 |

Regional partial-view accuracy is now exactly 80%, meeting that individual target.
The added building views fixed both prior relief misses. Regional embeddings miss
two close details from The Fall of Man and incorrectly select three negatives in
offline automatic-policy simulation. Regional simulated precision is 88.5%, below
the required 98%, and only 26 automatic selections are available, below the minimum
30. These held-out errors must not be moved into calibration and reused as evidence
for the same evaluation. They are regression cases for future method development.

The [evaluation report](visual-validation-v3-results.json) records all candidates,
scores and gate results. Dataset coverage also fails: evaluation has 12 of the
required 20 identities and still lacks required partial captures for several
existing identities. The minimum numerical gap is now 40 positive photos. A fresh
blind evaluation should collect all required views in new sessions rather than
relying on this inspected regression corpus.

## Atlas-backed API run

All 77 multipart requests returned HTTP 200. The API returned the expected object
in the top three for 25/27 evaluation positives; the same two painting details were
missed. Evaluation p95 was 1.752 seconds and full-run p95 was 1.806 seconds for
sequential in-process ASGI uploads, local CPU inference and real Atlas. Camera
capture, phone networking, startup and public HTTP transport are excluded.

The API produced zero automatic matches because it remains candidate-only. Saved
API candidates and scores exactly reproduce the offline regional ranking and policy
simulation. That comparison does not enable the policy or turn simulated selections
into user-visible automatic results.

Artifacts: [manifest](visual-validation-v3-manifest.json),
[audit](visual-validation-v3-audit.json),
[frozen calibration](visual-validation-v3-calibration.json),
[API report](visual-validation-v3-api.json), and
[integrity summary](visual-validation-v3-summary.json).

## Remaining completion work

The collection constraint has changed: no visitor will be asked to create new
photos. The [online acquisition decision](13-visual-online-photo-acquisition.md)
keeps the Rijksmuseum pilot for a bounded public-source audit and defines when a
location change is justified. Public files must pass identity, license,
independence and camera-capture review; raw search results do not close the gap.

1. Collect at least 40 additional independent positive photos: eight more identities
   with one full and two partial captures each, plus the missing partial captures
   for current identities. The [field checklist](11-visual-field-photo-checklist.md)
   lists source-covered candidates.
2. Collect a larger, representative calibration-negative set in sessions separate
   from evaluation.
3. Improve rejection and detail recognition using the failed cases as development
   regressions, then freeze a new policy before using fresh held-out photos.
4. Meet every documented accuracy gate, including at least 30 automatic selections,
   at least 98% precision and zero negative automatic errors in the pilot sample.
5. Run the accepted policy through the real API and measure end-to-end latency on
   the intended phone and network. This environment cannot perform physical capture
   or phone/network measurement.

Verification: 230 tests passed, one opt-in live fixture skipped; Ruff lint and
formatting and `mypy app` passed. Automatic visual identification remains disabled.
