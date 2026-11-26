# iOS on-device recognition — discovery (2026-11-26)

Evidence gathered from `main` at `9452213` for the brief *Museum recognition on
iPhone 18 Pro*. Inspected from source only: no MongoDB access, no model weights
downloaded, no Mac/Xcode or physical device used. Nothing below is a measured
iOS result.

## 1. Headline findings

1. **There is no CLIP model in this project** (confirmed by the owner on
   2026-11-26; no source, dependency or history references it). The brief's
   "DINOv2 + OpenCV + CLIP" baseline is corrected to the implemented stack:
   **DINOv2-small + OpenCV (SIFT/RANSAC verification and contour region
   proposals)**. SAM2 is used only server-side in admin reference capture, not
   in visitor recognition.
2. Recognition today is **server-side** (Cloud Run, ONNX Runtime CPU). The
   visitor client is a **browser web app** (`app/modules/live_camera/static`),
   not a native iOS project. There is no Swift/Xcode code to reuse.
3. Retrieval is **exact brute-force in NumPy over an in-process snapshot**, not
   Atlas Vector Search. Score semantics are raw cosine (dot product of
   L2-normalized vectors); the MongoDB `(1 + cos) / 2` transform does not apply.
4. A packaged phone-catalog export already existed for an earlier trial
   (`live_camera/export.py`: ONNX model + `catalog.json`, 7-day expiry). It is a
   useful starting point for the venue package, but is limited to 2–12 objects.
5. Automatic identification is **disabled everywhere** and the decision policy
   is explicitly **uncalibrated** (`calibrated: False`).

## 2. Recognition pipeline as implemented

Entry point: `CameraRecognizer.recognize` (`live_camera/inference.py`); with the
adaptive setting it delegates to `live_camera/pipeline.py`
(`PIPELINE_VERSION = "object-regions-types-v5"`).

| Step | Implementation | Source |
|---|---|---|
| Frame capture (client) | Center square of side `min(w,h)·224/256`, drawn to a 224 or 512 px canvas, JPEG q=0.9 | `static/capture.mjs` `drawFrame` |
| Quality gate | Reject frame if max per-channel stddev < 2 (`unreadable`) | `pipeline.py` |
| Region proposals (OpenCV) | 224 px working copy → gray → Gaussian 3×3 → Canny(40,120) → close; plus LAB contrast vs. border median (>max(20, p55)); contours, area 0.08–0.85, fill ≥ 0.25, padded, centre-weighted; up to **2** boxes, IoU dedupe | `regions.py` |
| Boxes evaluated | Full frame + proposals (if `object_regions`) | `pipeline.py` |
| Crop | Normalized box crop, resized back to frame size (bicubic) | `regions.crop` |
| Embedding | Resize to 224×224 **bicubic**, RGB /255, ImageNet mean/std, NCHW → DINOv2 CLS token → L2 normalize | `pipeline.embedding` |
| Retrieval per object | For each view: `s = view_matrix @ q` over its region vectors; view score = `0.75·s[0] + 0.25·max(s)`; object score = max over views | `pipeline.py`, `inference.py` |
| Planar verification (OpenCV) | Types painting/drawing/print/photograph: SIFT (800 features) on the region; BFMatcher k=2, ratio 0.7, one match per reference point, ≥ 8; `findHomography` RANSAC 4.0 px, seed 0; score = `inliers/good · min(inliers/20, 1)`; accepted if ≥ 0.4, and **replaces** the embedding score for planar objects | `features.geometric_evidence`, `pipeline.py` |
| Multiview objects | Embedding score only | `pipeline.profile` |
| Aggregation | Best score per **distinct object** across boxes; top 3 | `pipeline.py` |
| Decision | `candidate` if per-profile thresholds `enabled`, coverage ready, score ≥ floor, margin to 2nd distinct object ≥ margin, within time budget; else `ambiguous` (≥ 0.45) / `no_match` / `deferred` (budget exceeded, default 1500 ms) | `pipeline.py` |
| Temporal | Policy `stable_frames: 3`, `minimum_stable_ms: 600` (applied client-side) | `references.py`, static JS |

Default policy (`live_camera/references.py`): `candidate_floor 0.55`,
`candidate_margin 0.04`, `stable_frames 3`, `global_weight 0.75`,
`geometry_min_score 0.4`, `calibrated False`. Per-profile thresholds come from
an optional frozen calibration artifact bound to model hash, reference
signature and a hash of the pipeline source (`policy.py`).

## 3. Models

| Role | Model | Pin | Notes |
|---|---|---|---|
| Visual embedding | `facebook/dinov2-small` (Hugging Face `transformers`) | revision `ed25f3a31f01632728cabb09d1542f84ab7b0056` | 384-d, CLS token of `last_hidden_state`, no register tokens, no projection head |
| Admin capture masks | `facebook/sam2.1-hiera-tiny` | revision `de431c40…`, safetensors SHA-256 `48c14467…` | Server only; prepares object-focused reference images. Not needed on the phone |
| CLIP | **none found** | — | See blocker |

Existing conversion: `export.py` wraps DINOv2 as `pixel_values[1,3,224,224] →
normalize(CLS)` and exports ONNX opset 17 with a max-abs parity check ≤ 1e-4.
The same wrapper is the natural source for a direct PyTorch → Core ML
conversion (coremltools); parity against PyTorch must be re-measured.

### Preprocessing discrepancy to resolve

- **References** are embedded with `AutoImageProcessor` for dinov2-small
  (expected: short edge → 256 bicubic, center crop 224; to be confirmed from the
  pinned `preprocessor_config.json`), on the full image and four 65 % regions.
- **Camera frames** are pre-cropped by the client to the central `224/256`
  square, then resized directly to 224 bicubic on the server.

The two paths are approximately but not exactly equivalent. The iOS port must
reproduce the **camera** path (it is what the thresholds were tuned on), and the
golden fixtures must record both.

## 4. MongoDB data (from code, not from the live database)

Collections used: `locations` (`status: active`), `objects` (`location_id`,
`name`, `type`, `creator`, `year`, `description`, `external_id`,
`source_ids`), `sources` (`title`, `url`, `raw_record`), `visual_references`,
and a per-vector collection keyed by `set_id`/`view_id` (`visual/repository.py`).

`visual_references` record (per object):

- `object_id` (string ObjectId), `location_id` (string), `set_id`,
  `model` (`facebook/dinov2-small@<revision>`), `preprocessing`
  (`rgb-thumbnail2560-dino224-regions65-sift800-v1/<library versions>`),
  `evidence_hash`.
- `views[1..4]`: `reference` (`source_ids`, `image_url`, `viewpoint`,
  `rights_status`), optional `image_hash`, and `derived`:
  - `regions[1..5]`: `{box, embedding: float[384]}` stored as **plain arrays**
    (no BSON binary vectors observed in code);
  - `local`: SIFT `points[≤800][2]`, `descriptors[≤800][128]`, `size` (≤ 800 px).

Consistency: the existing export re-reads objects/records after building the
snapshot and fails on any `set_id`/`evidence_hash` change; the server computes a
`reference_version` hash. Production holds **115 objects** (doc 34). The
reference `preprocessing` string embeds library versions, so a dependency bump
invalidates references by design.

Package size estimate (115 objects × ≤ 4 views): embeddings ≤ 115·4·5·384·4 B ≈
3.5 MB; SIFT descriptors ≤ 115·4·800·128·4 B ≈ 188 MB as float32 (worst case).
SIFT descriptors are 0–255 integers in practice; storing them as `uint8` would
be ≈ 47 MB but must be verified for exactness before adoption.

## 5. iOS mapping

| Python | iOS |
|---|---|
| ONNX Runtime DINOv2 | Core ML model converted from the same PyTorch wrapper |
| OpenCV Canny/LAB/contours, SIFT, BFMatcher, findHomography | OpenCV iOS framework (core, imgproc, features2d, calib3d) behind an Objective-C++ bridge; SIFT is in main OpenCV ≥ 4.4, no contrib needed |
| NumPy dot products | Accelerate (CPU baseline) and MPS matrix-vector (GPU) |
| PIL bicubic resize, JPEG round-trip | Must be matched or parity re-measured: the server sees a **JPEG-decoded** frame, the phone will not |
| Client stable-frame logic | Swift decision engine |

## 6. Environment

- Backend: Python ≥ 3.12, FastAPI, PyMongo async, deployed on Cloud Run.
- Extras: `visual` (torch, transformers, opencv), `camera-server` (onnxruntime).
- No iOS project, no Swift package, no Xcode settings in the repository.
- Hugging Face is not reachable from this session's network, so the pinned
  DINOv2 weights and `preprocessor_config.json` could not be fetched; model
  conversion must run where they are available (e.g. the user's Mac).
- Device/toolchain: **not yet available to this session.** iOS builds require
  the user's Mac with Xcode; nothing here was compiled for iOS.

## 7. Blockers and decisions needed

1. ~~CLIP~~ — resolved: not used; the port covers DINOv2 + OpenCV only.
2. **Publishing descriptors.** `server.py` currently states "Model and
   reference descriptors stay on the server". Offline recognition requires
   shipping derived DINOv2 vectors and SIFT descriptors (not photos) to phones.
   This is a product/rights decision for the owner.
3. **MongoDB read access** (sanitized sample or read-only credentials on a
   backend environment) to verify the schema above against real records.
4. **Mac with Xcode and the target iPhone** for building, Core ML performance
   reports and device validation.
5. **Calibration data**: the current policy is uncalibrated; held-out museum
   photos/videos are needed before any confident on-device identification.
