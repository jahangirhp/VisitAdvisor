# Phase 5 online validation v5 — 2026-09-18

V5 tested the global/regional ensemble and broader multi-view references on a new,
source-separated public-photo corpus. The dataset and FastAPI replay are complete,
but the recognition acceptance gates fail. Automatic visual identification remains
disabled; the API continues to return candidates for visitor confirmation.

## Dataset and references

The frozen manifest contains 180 photos:

- calibration: 52 positives and 38 negatives;
- held-out evaluation: 60 positives across 20 identities;
- every held-out identity: one full view and two partial views from separate source
  pages;
- held-out negatives: 30 examples across outside-reference, similar-object,
  wrong-location, and uninformative categories;
- object types: paintings, sculptures, and buildings.

All 90 evaluation photos come from unique Wikimedia Commons source pages. None of
those pages overlaps the calibration sources or the promoted reference sources.
The audit found no raw or normalized duplicate images and every dataset coverage
gate passes. Public metadata and the recorded source review do not substitute for
a future physical-phone trial.

Two evaluation identities initially lacked active descriptors. The Milkmaid and
The Jewish Bride were prepared with two source-linked views each, without retaining
pixels in Atlas. The final frozen snapshot contains 112 active reference sets for
128 objects. Calibration and validation bind those exact set IDs.

## Frozen method

The tested regional score is 75% whole-image similarity and 25% best regional
similarity. Calibration fitted an automatic score threshold of `0.70` and a
top-two margin of `0.04`. On the 90 calibration photos it made 27 automatic
decisions, all correct, covering 51.9% of calibration positives.

The held-out results were:

| Baseline | Positive top 3 | Automatic correct/selected | Negative automatic errors |
|---|---:|---:|---:|
| Global embeddings | 41/60 | 28/31 | 3/30 |
| Regional ensemble | 42/60 | 29/34 | 4/30 |
| Local features | 32/60 | 31/33 | 0/30 |

Regional automatic precision was 85.3%, below the required 98%. Four negatives
were selected automatically, while the gate requires zero. Full-view top-three was
16/20 and partial-view top-three was 26/40, below the required 18/20 and 32/40.
Positive automatic coverage reached the 50% target, but the other recognition
gates prevent activation.

The hard-negative failures are useful evidence. Three similar or outside-reference
artworks were assigned to existing identities, and one Night Watch photo was
assigned to the Night Watch Room. Several architectural scenes and digitally
cropped paintings also fell outside the correct top three. These results show that
the current descriptors still confuse object content with surrounding room or
building context.

## FastAPI replay

The initial sequential replay returned HTTP 200 for 89/90 multipart uploads; one
request encountered a transient Atlas timeout and safely returned 503. Its single
retry returned HTTP 200. The completed replay emitted 63 `ambiguous` and 27
`no_match` responses, with zero automatic matches. Candidate order matched the
offline regional result on all 90 successful requests.

The first replay also exposed and fixed a score-aggregation mismatch when the best
global and regional scores came from different reference views. After the fix, the
maximum absolute API/offline score difference was `1.60e-7`, and backend p95 was
2.085 seconds. This includes local CPU inference and real Atlas queries, but
excludes camera capture, mobile networking, and public HTTP transport.

## Decision and next work

Phase 5 is not complete. Keep candidate-only behavior and use the v5 misses as a
fixed regression set. The next recognition iteration should improve reference
coverage and ranking, especially separation between an artwork and its room,
buildings versus unrelated rectangular images, and similar paintings. Thresholds
must then be fitted on a broader calibration corpus and evaluated on another unseen
source-separated corpus. Only after the recognition gates pass should the same
frozen policy be exposed through automatic API responses and tested on a physical
phone and intended network.

Artifacts: [manifest](visual-validation-v5-manifest.json),
[dataset audit](visual-validation-v5-audit.json),
[calibration](visual-validation-v5-calibration.json),
[held-out results](visual-validation-v5-results.json),
[API replay](visual-validation-v5-api.json),
[source audit](visual-validation-v5-source-audit.json),
[credits](visual-validation-v5-credits.json), and
[integrity summary](visual-validation-v5-summary.json).
