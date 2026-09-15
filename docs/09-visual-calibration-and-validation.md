# Phase 5 calibration and validation — 2026-09-15

The calibration workflow is implemented and exercised against real Atlas references.
Phase 5 remains incomplete: the evaluation corpus does not meet its coverage and
sample-size gates. Automatic API identification remains disabled.

## Operator workflow

Prepare one manifest containing calibration and evaluation photos before fitting.
The existing `Photo` schema and manual reference preparation apply. `object_type`
annotations must be consistent for each object; the pilot requires positive
paintings, sculptures, and buildings, and may include other object types.

```bash
# Local files only: no Atlas connection, model inference, or provider call.
.venv/bin/python -m app.modules.visual audit \
  --manifest photos.json --output dataset-audit.json

# Fit on calibration frames only. seed-policy.json fixes the model, preprocessing,
# and retrieval floors; its automatic score/margin values are replaced by fitting.
.venv/bin/python -m app.modules.visual calibrate \
  --manifest photos.json --policy seed-policy.json --output calibration.json

# Reuse exactly that policy, manifest, images, and reference-set versions.
.venv/bin/python -m app.modules.visual validate \
  --manifest photos.json --calibration calibration.json --output validation.json
```

All output paths must be new: commands refuse to overwrite frozen artifacts.
`audit` exits zero when inspection succeeds; check its readiness fields for missing
data. `calibrate` requires independent positive and negative calibration photos,
and checks their reference coverage before fitting. This is a minimum to run the
experiment, not evidence that a small calibration sample is representative.
`validate` writes the report and exits one when the regional recognition gates
fail. It still never enables the API, even if the offline gates pass.

The audit lists missing full/partial captures per object, missing identities,
negative categories, and object types for each split. At least 20 evaluation
identities need one full capture and two partial captures, all from distinct
capture groups. At least 30 negative photos must span `outside_reference`,
`similar_object`, `wrong_location`, and `uninformative`. Viewpoint, object identity,
and independence annotations require human review; local audit does not establish
Atlas reference coverage or prove that a photo was taken by a visitor.

## Fitting and leakage checks

Calibration compares the existing global, regional, and local baselines. It keeps
each retrieval floor fixed and searches automatic similarity thresholds in 0.01
increments, and score margins from 0 to 1 in 0.02 increments. It maximizes correct
automatic selections while permitting zero incorrect selections on calibration
photos. Ties favor the stricter score threshold, then stricter margin. A method
without a usable operating point is explicitly disabled. Scores are similarities,
not probabilities. The policy's `automatic_enabled` field controls the offline
simulation only; it is not an API feature flag.

The resulting artifact retains model/preprocessing identities, thresholds,
calibration metrics, manifest digest, raw and normalized photo hashes, reference
set IDs, creation time, and an integrity digest. Only calibration frames are
encoded for fitting. Evaluation frames are inspected for file validity and leakage,
but their matching scores do not participate in threshold selection.

Validation rejects changed policy artifacts, annotations, splits, photo files,
or reference sets before evaluation inference. Changed files during processing
are rejected too. Exact and normalized duplicates cannot cross splits or inflate
photo counts within a split; metadata-only variants of the same decoded image are
therefore caught. Reference images and their normalized hashes cannot count as
independent queries, including references belonging to another queried location.
These checks do not detect every crop, resize, recompression, or mislabeled capture:
all derivatives still need the same capture-group annotation and provenance review.
The integrity digest detects edits; it is not a signature or proof of truthful labels.

The older `benchmark` command remains useful for exploratory comparisons, but a
handwritten threshold file cannot pass the new `calibration_policy_verified` gate.
Use `validate` with an artifact produced by `calibrate` for the verified workflow.
Offline success only means readiness to validate that regional policy through the
API. It does not establish phone/network latency or complete Phase 5.

## Real regression exercise

The [new manifest](visual-calibration-manifest.json) preserves all 45 previous
rows and adds one independently photographed sculpture as a calibration negative:
Guanyin, photographed by Dqfn13, licensed CC BY-SA 4.0. This sculpture is outside
the frozen 85-object reference corpus. Its [source and credit record](visual-calibration-added-source.json)
retains licensing, metadata, capture-date ambiguity, and the downloaded image hash.
No existing held-out photo was moved into calibration or relabeled. No reference
images, embeddings, or canonical Atlas records were changed for this exercise.

The new thumbnail is about 442 KB and stays in the ignored local evaluation folder.
It is test data, not an expansion of the production reference-image archive. The
previous evaluation photos have already been examined, so this run is a regression
exercise, not a fresh blind field test.

Calibration used eight positive photos across four painting identities and one
negative photo. The fitted global/regional policies selected all eight positives;
local features selected six. All abstained on the calibration negative. One negative
is insufficient to establish reliable rejection of diverse unknown objects.

Evaluation used the unchanged 37-photo split: five positive photos and 32 negatives.

| Baseline | Positive top 3 | Simulated automatic correct/selected | Negative automatic errors |
| --- | --- | --- | --- |
| Global embeddings | 5/5 | 3/3 | 0/32 |
| Regional embeddings | 5/5 | 3/3 | 0/32 |
| Local features | 5/5 | 5/5 | 0/32 |

Artifacts: [frozen calibration](visual-calibration-policy.json),
[evaluation report](visual-calibration-validation.json), and
[dataset audit](visual-calibration-dataset-audit.json).
All recognition gate groups fail overall. Three or five correct automatic choices
cannot pass the required minimum of 30 automatic choices. The corpus also lacks
held-out partial views, positive sculpture/building coverage, and wrong-location
negative examples. No thresholds were adjusted after seeing these results.

The remaining evaluation collection work is at least 15 additional identities,
with a full photo and two independent partial captures each, plus two partial
captures for each of the existing five evaluation identities: at least 55 additional
positive photos. Add a distinct wrong-location negative and broaden calibration
negatives. New object types also need their own source-linked reference coverage.
Freeze a new corpus and recalibrate before treating that evaluation as evidence.

## Reproduce this run

Run the manifest beside its local images, not the audit copy under `docs`:

```bash
OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENAI_API_KEY='' \
  .venv/bin/python -m app.modules.visual calibrate \
  --manifest .local/visual-public-photos/calibration-manifest-v1.json \
  --policy .local/visual-public-photos/policy.json \
  --output calibration-rerun.json

OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENAI_API_KEY='' \
  .venv/bin/python -m app.modules.visual validate \
  --manifest .local/visual-public-photos/calibration-manifest-v1.json \
  --calibration calibration-rerun.json --output validation-rerun.json
```

Verification: 227 tests pass; Ruff lint/format and `mypy app` pass. Tests cover fitting
without false calibration matches, abstention when no threshold works, separate
inference for the two splits, frozen artifacts, changed photos/reference sets,
normalized duplicates, distinct capture coverage, and the required object types.
Existing live Atlas latency results remain documented separately. No API runtime
ranking or automatic-selection behavior was changed in this step.
