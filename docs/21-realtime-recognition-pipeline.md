# Bounded real-time recognition prototype

This document records the 2026-10-13 implementation and measurements. Subsequent
changes and limitations are in [recognition reliability](22-recognition-reliability.md).

This Phase 5 implementation adds an opt-in camera pipeline for the selected
location and 2–12 explicitly configured objects. It does not guarantee recognition
of every object, angle, occlusion or lighting condition. Automatic identification
remains disabled, and visitor confirmation remains required.

Set `LIVE_CAMERA_ADAPTIVE=true` and leave
`LIVE_CAMERA_GEOMETRY_RERANK=false`. Existing camera behavior stays the default.
The pipeline uses the existing local embedding model and Atlas references; it
does not call a paid provider or save visitor frames.

## Implemented behavior

- Paintings, drawings, prints and photographs use planar geometric verification
  against the same reference view that produced the embedding score.
- Other object types use multi-view embedding comparison. They require at least
  two distinct reference image hashes and two distinct labeled viewpoints before
  establishing a suggestion. This minimum does not prove all-angle coverage.
- Optional `LIVE_CAMERA_OBJECT_REGIONS=true` adds at most two OpenCV contour and
  contrast proposals. These are cheap regions, not a trained object detector.
  Each frame uses at most three model calls; the default uses one.
- Featureless frames abstain. Ambiguous scores, inadequate references and frames
  exceeding `LIVE_CAMERA_INFERENCE_BUDGET_MS` cannot establish a suggestion.
  The budget is checked between native operations; it cannot interrupt an ONNX
  or OpenCV operation already executing.
- The browser requires three agreeing results spanning at least 600 ms, resets
  acquisition on movement or region changes, and retains the existing brief weak
  frame grace. Repeated frames are not independent accuracy evidence.
- The server warms the model before catalog readiness and scanning. `GET /ready`
  checks readiness. Optional preload runs in the background. Cold preparation
  time is measured separately from warm request latency.
- Atlas reads project only required reference fields, including planar local
  descriptors only where needed. Source records, evidence and rights are not
  modified or replaced by derived predictions.

## Reproduce and calibrate

Use a source-reviewed `ReplayManifest`, with explicit camera object IDs, frozen
  photo hashes, capture groups and disjoint calibration/evaluation splits:

```bash
python -m scripts.camera_pipeline_eval --manifest MANIFEST.json --mode adaptive \
  --phase replay --output replay.json
python -m scripts.camera_pipeline_eval --manifest MANIFEST.json --mode adaptive \
  --phase calibrate --policy-output preview-policy.json --output calibration.json
python -m scripts.camera_pipeline_eval --manifest MANIFEST.json --mode adaptive \
  --phase validate --calibration preview-policy.json --output validation.json
```

Outputs must be new files. Calibration fits separate planar and multi-view
thresholds using only the calibration split. A profile needs at least ten
positive samples, ten negatives and two identities; insufficient evidence or no
safe operating point disables that profile. The artifact binds the reference
signature, model hash, pipeline code, region setting, budget and dataset hashes.
Set `LIVE_CAMERA_CALIBRATION` to that artifact for the experimental preview.
Changing bound inputs requires another calibration. This is preview calibration,
not a claim of population-level reliability.

Replay exercises the actual browser crop/JPEG path and HTTP camera handler.
Reports retain failures in accuracy denominators and latency measurements,
record integrity checks, and distinguish successful diagnostics from release
acceptance. Validation exits nonzero until release gates pass. Physical phone
network latency and motion tracking are not measured by static-photo replay.

## Current evidence and remaining work

The frozen historical regression corpus contains 119 photos: 51 positives across
12 identities and 68 negatives. It excludes labeled positives outside this
camera catalog without relabeling them as unknown. It is not a new holdout.
Adaptive replay completed 119 valid HTTP responses: top-one 34/51, top-three
40/51, warm request p95 212 ms, one model call per frame. Uncalibrated preview
suggestions had only 25/32 correct results and four false suggestions on unknown
photos. These results fail acceptance.

Adding regions gave top-one 35/51 and top-three 42/51, p95 420 ms, at most three
model calls, and the same 25/32 suggestion precision. Regions therefore remain
off by default. Adaptive cold preparation took about 17 seconds; this does not
meet an immediate first-launch promise.

Calibration-only replay succeeded after earlier Atlas readiness failures. Its
planar profile is disabled (six calibration positives, below the minimum ten).
The multi-view preview profile fitted floor 0.69 and margin 0.04, selecting only
four correct calibration positives and no incorrect suggestions. This small
sample does not establish release precision. Full measurements and immutable
input identifiers are preserved in the
[regression evidence](realtime-recognition-regression-2026-10-13.json).

The separate historical evaluation split completed 63 valid requests with all
integrity checks passing: three correct suggestions, zero false suggestions on
30 negatives, but only 3/33 positive suggestion coverage (9.1%). Warm request
p95 was 141 ms. Validation correctly exited with failure because release gates
remain unmet. Three correct suggestions are far below the precision sample
requirement and cannot establish reliable recognition.

Shiva currently has one distinct source image and no labeled viewpoints. Acquire
and review additional source-linked views, preserving rights and provenance,
before enabling its suggestions. Do not promote evaluation images into reference
data without retiring those images and capture groups from future evaluation.

Release still needs fresh independent online-photo holdout coverage, safe
precision and coverage, zero unknown false suggestions, and actual phone tests
for end-to-end p95 latency and tracking through motion, occlusion and switches.
The broader 20-identity acceptance target requires multiple camera cohorts because
one configured camera catalog is limited to 12 objects. Ticket #9 remains open.
