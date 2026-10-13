# Artwork and room confusion — ticket #9

Development date: 2026-10-13. Status: implementation and development regressions;
**independent acceptance is pending**. Ticket #9 remains open. This work stays in
Phase 5 and does not enable automatic identification.

Branch: `MVP-Fix-artwork-and-room-confusion-in-visual-recognition`. Git does not
allow the spaces and square brackets in the literal issue title.

## Findings and implementation

The existing 50-painting diagnostic reproduces 42/50 correct top-one and top-three
rankings, including the Night Watch/room and Syndics/gallery substitutions.
In the separate positive2 batch, only 3/9 paintings rank correctly first.
Whole-image embeddings can favor surrounding rooms even when SIFT descriptors
strongly match the actual artwork. Matches inside room references can occupy
less than one percent of the reference: they support the depicted painting,
not identification of the room containing it.

The default camera now verifies geometry on the **same reference view** that
supplies its embedding score. Previously, geometry from one view could validate
an unrelated view's higher score. A regression test demonstrates this defect.
The Atlas camera policy includes `geometry_scope=reference-view-v1`, changing
the response version so a browser reloads metadata rather than mixing policies.

Two additional experiments are explicitly opt-in:

- Uploads: `VISUAL_GEOMETRY_OBJECT_IDS` selects at most twelve current objects at
  the requested location. Geometric candidates can recover artworks absent from
  the embedding top three. A selected `building` reference's geometric score is
  multiplied by the square root of its inlier-hull area divided by reference area.
  This reduces evidence confined to one painting inside a room. Unselected
  objects retain the existing embedding retrieval. The response identifies
  `method=visual_embeddings_geometry` and `VISUAL_GEOMETRY_EXPERIMENTAL`.
- Camera: `LIVE_CAMERA_GEOMETRY_RERANK=true` applies that spatial geometric score
  to the existing selected 2–12-object camera catalog. Its public policy records
  `geometry_ranking=spatial-verification-v1`, included in the reference version.

Both experiment settings default off. These are **new uncalibrated score
semantics**, not transferred v5 confidence or probabilities. The existing numeric
geometry gate, display floor, margin and tracking requirements are not lowered.
Planar matching does not establish arbitrary sculpture or 3D recognition.

The upload descriptor cache contains only the explicit subset, keyed by immutable
reference-set IDs, serialized through the existing inference lock. Every request
still checks current source evidence, selected location and active sets before
and after ranking. Missing/stale/invalid records invalidate the result; descriptor
read failures clear the cache. Cold loading has a separate 30-second bound;
ordinary metadata checks retain their existing database timeout. No query pixels,
raw source records or generated stories are cached. Atlas remains canonical and
all experiments perform read-only operations.

## Recorded regression results

Control and experiment used identical Atlas reference-set IDs on positive2.

| Upload slice | Embedding control top one | Geometry experiment top one |
|---|---:|---:|
| All positive2 photos | 42/50 | 48/50 |
| Paintings | 3/9 | 9/9 |
| Buildings | 39/41 | 39/41 |

Overall top-three improved from 45/50 to 49/50. Both complete runs returned HTTP
200 for all fifty requests. Sequential in-process p95 was 2.083 seconds for the
control and 2.270 seconds for the experiment. The experiment's first cold upload
took **11.564 seconds**; this remains an interactive-startup limitation.

The initial uncached experiment returned safe 503 errors on seventeen attempts
and was stopped. Those attempts are retained separately, rather than omitted or
presented as successful rejections. The later complete run includes its cold
request in the latency distribution.

WebKit replay uses the actual shared `capture.mjs` crop and JPEG-quality code,
then calls the real camera HTTP handler and the upload API against live Atlas.
On nine previously inspected painting photos from positive2:

| Static replay metric | Control | Opt-in experiment |
|---|---:|---:|
| Upload top one | 3/9 | 9/9 |
| Camera top one | 3/9 | 8/9 |
| Camera top three | 9/9 | 9/9 |
| Correct camera policy suggestions | 3/9 | 7/9 |
| Wrong camera policy suggestions | 3 | 0 |

These suggestions simulate the frozen floor/margin on individual frames. They
are **not measured three-frame tracking or physical iPhone behavior**. No room-only
negatives are present in that nine-photo run. Its acceptance result is FAIL, not
PASS. The default camera also preserves the historical Pillow replay result:
3/6 correct suggestions and zero false suggestions on 84 negative cases. That
historical replay is a separate two-object, non-browser regression.

A subsequent WebKit run combined those nine paintings with the existing 53
unknown-object fixtures (mostly official reproductions, not independent room-only
captures). All 124 requests completed with HTTP 200. Camera top-one remained 8/9,
with zero static suggestions on 53 unknowns. Upload top-one remained 9/9, but seven
unknowns still crossed the simulated display floor/margin through unselected
embedding candidates. Upload candidate preview must therefore remain manual;
this experiment is not a validated automatic-identification policy. No room-only
acceptance gate is satisfied by substituting these unknown fixtures.

The machine-readable evidence is in `artwork-room-regression-2026-10-13.json`.
It preserves per-case results, input hashes, reference IDs, policy/model identity,
initial failures and explicit scope limits. Fixture licenses/provenance remain in
the original `tests/data/visual` manifests; none became canonical reference data.

## Reproduction

Use the existing visual/camera-server extras, cached DINO weights, ONNX model
metadata and a configured read-only Atlas connection. No OpenAI key is required.
Install the existing browser dependencies with `npm ci` and, if necessary,
`npx playwright install webkit`. Run from the repository root.

The existing positive-only control is:

```bash
.venv/bin/python -m scripts.visual_positive_test tests/data/visual/positive2 \
  --output /tmp/positive2-control.json
```

For the upload experiment, repeat `--verification-object-id ID` for every artwork
and relevant room to verify. The reported subset was Night Watch
`6aab0dbcdd73e04d96150a00`, Syndics `6aa9a745dd73e04d96146160`, Milkmaid
`6a9dd4126d52c3bc396586a0`, Gallery of Honour `6aab0dfd303f7d479a4f23e6` and
Night Watch Room `6aab0dfd303f7d479a4f23e8`. Readiness is checked against current
Atlas data; those IDs alone are not proof of current coverage.

`scripts.artwork_room_replay` accepts a frozen JSON object with `purpose`
(`regression` or `acceptance`), `independence_reviewed`, `camera_object_ids`, and
`photos`. Each photo uses the existing `Photo` fields from document 07 plus
`sha256`, `source_page`, `license`, and `room_only`. Paths are relative to the
manifest. A room-only negative has `expected_object_id=null` and `room_only=true`.
All expected artworks must compete together with the relevant room identities;
never restrict a request to its known answer. No frame files are written.

```bash
# First run the embedding/per-view-geometry control.
.venv/bin/python -m scripts.artwork_room_replay \
  --manifest /path/to/frozen-manifest.json --browser webkit \
  --output /tmp/room-control.json

# Then run the explicit geometry experiments against the same frozen inputs.
.venv/bin/python -m scripts.artwork_room_replay \
  --manifest /path/to/frozen-manifest.json --browser webkit \
  --verify-upload --verify-camera --output /tmp/room-experiment.json
```

Output files must be new. Exit 1 means the report was produced but gates failed;
exit 2 means setup/input execution failed. Missing samples, regression inputs,
zero suggestions, failed requests or changed inputs/references cannot pass.
The runner also rejects known historical image hashes as fresh holdout evidence;
near-duplicate/capture-session/source review remains a human responsibility.

## Remaining ticket acceptance

Freeze **new** independently sourced photos under the existing online acquisition
policy: at least thirty artwork-in-room captures across five artworks, six distinct
capture groups per identity, plus twenty room-only negatives. Preserve a separate
calibration corpus and do not tune on the release holdout. Include Night Watch and
another previously confused artwork, with every relevant room still competing.

Both paths need top-one >=90%, top-three >=95%, zero wrong suggestions and zero
suggestions on room-only negatives. Camera correct-suggestion coverage must be
>=50%. Report all failures, counts, confidence intervals and capture grouping.
The current nine-photo camera top-one result is 88.9%, below that target, and the
sample/independence/room-negative requirements are unmet. Existing broader Phase 5
and physical-phone gates remain independently required before automatic recognition.

Development checks: 293 Python tests passed, two opt-in tests skipped; Ruff
lint/format and `mypy app` passed; ten JavaScript and twenty Chromium/WebKit browser
lifecycle tests passed. The opt-in Atlas diagnostics above were run separately.
