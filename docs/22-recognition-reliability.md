# Recognition reliability work — 2026-10-14

The requested universal 100% recognition guarantee is not achieved. A camera
cannot recover an object's identity from absent, obscured or ambiguous evidence,
and a finite dataset cannot prove correctness in every situation. Automatic
identification remains disabled. Changes stay in Phase 5, on the existing ticket
branch, with the experimental camera pipeline opt-in.

## Concrete failures addressed

The previous adaptive pipeline discarded objects outside the embedding top three
before local feature verification. It also checked only the embedding-winning
reference view. Whole-scene embeddings can therefore prevent exact painting
details from being considered at all.

The replacement checks local details against every configured planar reference
view (maximum 12 objects, four views each). Planar candidate scores come from
that matched view's geometric evidence; a different view cannot lend unrelated
embedding similarity. Sculptures, artifacts and buildings keep multi-view
embedding matching. Distinct labeled views remain necessary for multi-view
suggestions. The missing Shiva reference coverage is not hidden or fabricated.

Adaptive camera frames are 512-pixel square JPEGs, while the embedding encoder
still receives a 224-pixel RGB tensor. Local matching retains the larger frame.
Legacy callers retain 224-pixel frames. The shared crop/JPEG code is used by both
the live camera and actual-browser replay. Requests remain bounded to 256 KiB,
with one in flight, no queue, no saved frames, at most three model calls, and
budget overruns disallowed from establishing a suggestion. Higher-resolution
frames increase upload and matching costs; physical phone latency is unmeasured.

The app now offers **Find an object by name**. Selecting a known catalog object
stops uploads and displays its existing source-linked information under
**YOU SELECTED THIS OBJECT**, rather than presenting it as a camera identification.
It works without camera permission once the catalog loads. It does not identify
unknown objects, guarantee a visitor's selection, or provide an offline catalog.

## Calibration data rather than a lower sample requirement

The original calibration split had only six painting positives, which disabled
that profile. Five previously reviewed, cached original camera photographs were
visually reviewed and added to calibration. Their original byte hashes, camera
metadata, photographers/uploaders, dates, source pages and licenses are retained
in `reliability-calibration-additions-2026-10-14.json`. Cropped diagnostic derivatives
were not used. Known same-session Vasyatka images and same-date bertknot images
were excluded. Original source bytes were copied without editing.

The existing 119 rows and the entire 63-photo evaluation split remain unchanged.
The new 124-row manifest remains a **historical development regression**, not
fresh acceptance evidence. This fixes sample insufficiency for experimental
preview fitting; it does not establish a reliable general-purpose operating point.

Calibration fingerprints now include the shared geometric-feature implementation
and browser capture code, in addition to the model, references and pipeline. Old
artifacts are rejected after these changes. Thresholds are fitted on calibration
photos only, before the separate evaluation run.

Without a fitted policy, the adaptive pipeline may return diagnostic candidates
but cannot establish a stable suggestion. The page directs visitors to manual
selection. `LIVE_CAMERA_ADAPTIVE` remains false by default; the experimental
preview must not be represented as a validated release.

## Reproduction and preserved evidence

The source data and comparisons are preserved in
[the evidence report](camera-reliability-regression-2026-10-14.json) and
[the frozen manifest](camera-reliability-regression-manifest-2026-10-14.json).
The manifest's paths are relative to the existing ignored public-photo cache,
not the `docs` directory. Reconstruct missing source photos using the original
credits and verify their hashes; changed or missing source bytes cannot pass.

```bash
cp docs/camera-reliability-regression-manifest-2026-10-14.json \
  .local/visual-public-photos/reliability-regression-manifest-v3.json
.venv/bin/python -m scripts.camera_pipeline_eval \
  --manifest .local/visual-public-photos/reliability-regression-manifest-v3.json \
  --mode adaptive --phase calibrate --policy-output /tmp/camera-preview-policy.json \
  --output /tmp/camera-calibration.json
.venv/bin/python -m scripts.camera_pipeline_eval \
  --manifest .local/visual-public-photos/reliability-regression-manifest-v3.json \
  --mode adaptive --phase validate --calibration /tmp/camera-preview-policy.json \
  --output /tmp/camera-validation.json
```

Output paths must not already exist. A successfully produced validation report
still exits 1 when release gates fail; setup failures exit 2. The policy is only
an experimental preview artifact. No database source/reference writes,
provider calls, deployment, commit or push were performed.

## Failed approaches and operational limits

All-view planar retrieval at 224 pixels produced top-one 36/51 and top-three
39/51 versus the previous 34/51 and 40/51. Increasing to 512 pixels improved
painting top-one from 12/21 to 15/21, but the mixed uncalibrated run achieved only
35/51 top-one, with seven unknown false suggestions. These comparisons are not
release acceptance and their scores are not identity probabilities.

Applying planar spatial geometry to room/building scenes reduced unknown false
suggestions to one but reduced mixed top-one to 24/51. That approach was rejected;
the final pipeline does not apply planar geometry to 3D or scene identities.

Several Atlas descriptor transfers failed during setup. Reference reads now use
required-field projections, at most three concurrent immutable-set reads, and
cancel remaining reads if one fails. Zlib wire compression uses the Python
standard library and official PyMongo async client. See the
[official network compression documentation](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/connection-options/network-compression/).
Large descriptor reads have a ten-second bound, complete cold preparation has a
60-second bound, and normal refresh keeps its 20-second bound. Metadata operations
retain their existing five-second client timeout. These are upper bounds, not
latency promises. Model readiness gates scanning; failed refreshes never use a
stale reference fallback. Cold preparation remains an unresolved usability limit.

## Remaining release criteria

Final frozen-policy evaluation completed 63/63 valid HTTP requests, with all
model/reference/code/photo integrity checks passing. The evaluation photos and
labels are the same as the previous run. Calibration data and preprocessing
changed, so this comparison measures the combined implementation/data change.

| Historical evaluation metric | Previous | Final |
|---|---:|---:|
| Positive top-one retrieval | 19/33 | 21/33 |
| Positive top-three retrieval | 23/33 | 24/33 |
| Correct preview suggestions | 3/33 | 13/33 |
| Incorrect preview suggestions | 0 | 2 |
| Unknown false suggestions | 0/30 | 1/30 |
| Warm HTTP p95 | 141 ms | 347 ms |

Final suggestion precision is 13/15 (86.7%), with a wide 95% Wilson interval of
62.1–96.3%. Correct suggestion coverage is 39.4%, below the required 50%. One
incorrect suggestion targets a known room photo and the other targets an unknown
object. These results fail reliability acceptance. The earlier zero-error result
made only three suggestions and did not establish high precision. There is no
claim of 100% reliability or of a fresh unseen holdout. Final cold preparation
took 12.1 seconds; phone/network/startup reliability remain unvalidated.

The final calibration run fits planar floor 0.85 / margin 0.32 and multi-view
floor 0.69 / margin 0.38, using only the calibration split. Evaluation results
were not used to change those thresholds. The evidence retains earlier failed
setup attempts, rejected experiments and the final validation report. Validation
correctly exits 1, while automated development checks pass: 314 Python tests,
two opt-in tests skipped, 15 JavaScript tests, 30 Chromium/WebKit browser tests,
Ruff lint/format, `mypy app` and standalone packaging/import checks.

Keep ticket #9 open until independent artwork/room acceptance, broader recognition
precision and coverage, and physical-phone tracking/latency pass together. The
historical evaluation must not be reused as a new unseen release holdout.
Unknown false suggestions, incorrect positive suggestions, abstentions, HTTP
failures, cold setup failures and warm latency are reported separately. A run
with zero suggestions cannot satisfy the recognition coverage requirement.
