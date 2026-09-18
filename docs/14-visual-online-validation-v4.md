# Phase 5 online validation v4 — 2026-09-17

This run is superseded by the source-separated
[v5 validation](15-visual-online-validation-v5.md). V4 remains a regression record.

The online-photo acquisition pass closed the Phase 5 dataset gap without asking a
visitor to take photographs. The frozen evaluation corpus now passes every dataset
coverage gate. Regional embeddings pass the full-view and partial-view retrieval
targets and make no false automatic selections, but the calibrated policy selects
too few photos to validate automatic identification. Phase 5 therefore remains in
candidate-only mode for a measured reason: automatic coverage and sample size.

## Catalog and references

Ten exact Rijksmuseum records were imported from their official accessions: The
Night Watch (`SK-C-5`), The Celebration of the Peace of Münster (`SK-C-2`),
Vanitas Still Life (`SK-A-5104`), the Memorial Guild Cup (`BK-1976-75`),
Crucified Christ (`BK-2015-57`), the Buglioni altarpiece (`BK-16978`), Spring
(`BK-NM-8672`), Autumn (`BK-NM-8674`), Summer (`BK-NM-8673`) and Apollo
(`BK-1961-50`). Their official images supply references independent of the public
query photographs.

Seven separately identified architectural spaces were added with licensed Commons
evidence and reference captures: the Atrium, Passage, Great Hall, Gallery of
Honour, Night Watch Room, Research Library and Room 0.1. Together with the existing
Rijksmuseum building, The Fall of Man and View of Itamaracá Island, this produces
the required 20 evaluation identities across paintings, sculptures and buildings.

All 17 new reference sets prepared successfully. Atlas now contains 128 objects,
126 sources, 110 active visual reference sets and 565 visual vectors. Atlas stores
derived descriptors and provenance; the downloaded query pixels remain in the
ignored local validation workspace. The ten official reference resolutions retain
`REFERENCE_RIGHTS_UNKNOWN`, so production reuse of those image files still needs a
rights review.

## Frozen dataset

The v4 manifest contains 110 independent public photographs:

- calibration: 12 positives and 8 negatives;
- held-out evaluation: 60 positives across exactly 20 identities;
- each evaluation identity: one full and two separately captured closer or oblique
  views;
- evaluation negatives: 30 across outside-reference, similar-object,
  wrong-location and uninformative categories;
- object types: painting, sculpture and building.

The audit found no duplicate image, split leakage or query/reference page overlap.
Every evaluation coverage gate passes. Source page, photographer, license, original
date when available, camera metadata, byte count and SHA-256 are recorded in the
[credits artifact](visual-validation-v4-credits.json). The corpus was source-audited
before inference, but public metadata cannot replace an independent human identity
review.

## Held-out results

Thresholds were fitted only on the 20 calibration photos and frozen before the
90-photo evaluation. The first CLI attempts encountered transient generic database
operation failures; direct reruns with identical frozen inputs completed, and no
dataset or threshold changed.

| Baseline | Full top 3 | Partial top 3 | Automatic correct/selected | Negative automatic errors |
|---|---:|---:|---:|---:|
| Global embeddings | 18/20 | 29/40 | 17/17 | 0/30 |
| Regional embeddings | 18/20 | 33/40 | 11/11 | 0/30 |
| Local features | 5/20 | 9/40 | 14/16 | 0/30 |

Regional embeddings pass both retrieval targets: 90% for full views and 82.5% for
partial views. Their simulated automatic selections are all correct, but 11
selections are below the required minimum of 30 and cover only 18.3% of positives.
The 100% observed precision therefore does not satisfy the precision gate's minimum
sample requirement. Automatic identification remains disabled.

The [held-out report](visual-validation-v4-results.json) preserves every ranking,
score, gate and latency measurement. Nine regional top-three misses remain useful
regression cases: two Fall of Man details, one Banquet detail, one Guild Cup view,
two Great Hall views, two Atrium views and one Great Hall full view.

## FastAPI run

The first API attempt occurred while Atlas was still publishing the new vector
entries and returned a safe 503. After the index caught up, all 110 sequential
multipart requests returned HTTP 200. Evaluation reproduced the offline regional
ranking exactly: 51/60 positives were in the top three. Score differences were at
most `1.39e-7`. Evaluation backend p95 was 2.463 seconds and whole-run p95 was
2.494 seconds. This includes local CPU inference and real Atlas, but excludes phone
capture, mobile networking and public HTTP transport.

The API returned candidates only and produced zero automatic matches, as designed.
See the [API artifact](visual-validation-v4-api.json) and
[integrity summary](visual-validation-v4-summary.json).

## Next implementation phase

The next phase is confidence calibration for usable automatic coverage. Build a
larger calibration split with at least 30 representative positives across the new
painting, sculpture and architectural identities, plus more hard negatives. Use it
to develop a calibrated global/regional ensemble and stronger multi-view references
against the preserved v4 failures. Then freeze the method and thresholds and run a
new source-separated held-out corpus; v4 must remain a regression set because its
results have now been inspected.

Only enable automatic identification after the new blind run produces at least 30
automatic selections, at least 98% precision, zero negative automatic errors and
the existing retrieval gates. A physical-phone run on the intended network remains
the final deployment validation; the backend measurement cannot substitute for it.
