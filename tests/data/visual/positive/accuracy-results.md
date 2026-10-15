# Positive photo recognition run — 5 October 2026

The opt-in Atlas visual API diagnostic completed successfully in 223.19 seconds.
All 105 registered photos received a valid result. The test command was:

```bash
RUN_VISUAL_IMAGE_CHECK=1 VISUAL_ENABLED=true \
  .venv/bin/pytest -s -q tests/test_visual_image_atlas.py
```

**42/50 added positive photos (84%) ranked the correct object first.** The correct
object was also in the top three for 42/50. The approximate 95% Wilson interval
for either proportion is 71.5%–91.7%, treating files as separate observations.
That interval overstates independence where photos share a capture session.

| Slice | Correct first | No candidates |
| --- | ---: | ---: |
| Full views | 31/33 | 0/33 |
| Partial views | 11/17 | 3/17 |
| Civic group portraits | 8/12 | 0/12 |
| Domestic interiors | 4/4 | 0/4 |
| Landscapes and seascapes | 13/13 | 0/13 |
| Other paintings | 14/18 | 3/18 |
| Individual portraits | 3/3 | 0/3 |

Eight positive photos missed. Three partial details returned no candidates: one
*Fall of Man* detail and two *Vanitas stilleven* details. One other Vanitas detail
ranked a different painting. Two banquet details ranked a portrait and an ewer
first, respectively. A Night Watch gallery photo ranked the Night Watch Room
first; a Syndics gallery photo ranked the Gallery of Honour first. These room and object confusions are
the clearest failure pattern in this run. No first-place error substituted a
different object within the same broad similarity group, but these groups are
not a controlled set of matched artwork pairs.

The two older positive fixtures ranked correctly (2/2). Of 53 existing negatives,
10 returned no candidates and 43 returned at least one candidate. A returned
candidate is a retrieval false candidate, **not** an automatic identification or
a displayed camera suggestion. The API intentionally leaves automatic visual
identification disabled. This diagnostic pytest test passes when requests and
integrity checks succeed; it reports recognition errors without failing by default.

This run used the global embedding candidate-preview API with its 0.45 retrieval
floor. It did not exercise the server camera's 224-pixel crop, JPEG preprocessing,
geometry gate, display floor/margin or three-frame tracking. Forty-one of the 50
photos appeared in earlier corpora, and several images share a photographer and
capture day. The batch is regression evidence, not a blind holdout or completion
of the [similar-item acceptance test](../../../../docs/19-similar-item-accuracy-test.md).
The proposed five paired identities, balanced unseen captures and similar-object
negatives still need their own run.

The [machine-readable result](accuracy-results.json) contains every photo's
expected ID, status, ranked candidates, counts by object/view/group, run time and
fixture-manifest hash. Candidate scores in that file are rounded to three decimal
places by the existing diagnostic test.
