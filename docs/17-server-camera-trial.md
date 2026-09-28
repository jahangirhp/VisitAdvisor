# Server camera trial — 2026-09-20

> Reference loading and deployment are superseded by
> [Atlas-backed camera references](18-atlas-camera-references.md). The results below
> describe the historical recorded-source bundle.

The user explicitly replaced the on-device requirement with server recognition.
This supersedes the local-inference and no-upload requirements in documents 01,
02, 07 and 16 for the current camera client. The scope remains the same two-artwork
recorded-source Rijksmuseum demo. Phase 5 accuracy gates remain unmet.

## Runtime

The browser crops the center guide to 224 × 224 pixels and encodes a JPEG at quality
0.9. While Start is active it posts selected crops to the same-origin `/recognize`
endpoint, with a target interval of 500 ms, a minimum 250 ms gap after each response,
and only one request in flight. It never downloads a model, WASM runtime or reference
vectors. Stop, page exit and backgrounding abort the current client request, stop
camera tracks and prevent later uploads. Late responses cannot restore results.
An already-running server inference may finish after a client disconnect.

The privacy notice explicitly states that crops leave the phone. No recording,
image files, database writes or frame-body logs are created. Images and derived
query tensors exist in process memory only. Google Cloud request logs retain normal
HTTP metadata, not image bodies. There are no external OCR, embedding or LLM calls.

The FastAPI process loads the existing hash-verified ONNX model once, using native
ONNX Runtime on CPU with one inference thread. JPEG pixels use RGB ImageNet
normalization and the same per-view 75% global / 25% regional reference ranking.
JPEG compression changes query pixels; this is a new transport/preprocessing path,
not a claim of exact browser-tensor parity or improved independent accuracy.

The browser retains the existing tentative-candidate thresholds and stability logic:
three agreeing frames to acquire a match, brief retention for minor jitter,
1.5-second weak-view grace, and immediate clearing for a different candidate or
large movement. Results more than three seconds old cannot establish a match.
Scores remain uncalibrated similarities, not confidence probabilities.

## API boundaries

- `GET /bundle/catalog.json`: current small public object/source metadata, policy,
  reference version and expiry. No model or reference descriptors.
- `POST /recognize?location_id=…&reference_version=…`: raw `image/jpeg` body;
  no multipart parsing or temporary upload files.
- Responses include version, location, reference version, up to three candidates,
  inference time and `automatic_identification=false`.
- Only 224 × 224 JPEGs are accepted. Actual streamed body is capped at 128 KiB,
  regardless of Content-Length. Body reception has a five-second deadline.
- The server checks selected location, snapshot version and expiry before inference;
  expiry is checked again afterward. Invalid or stale references never match.
- One frame may occupy an instance at a time. Starts are limited to at most four
  per second globally per instance; contention returns 429 with Retry-After.
  This is a bounded public trial, not authenticated per-user production limiting.
- No CORS is enabled. Cross-site browser frame submissions are rejected. These
  controls do not prevent non-browser clients from invoking the public trial.
- Model/runtime downloads return 404, and writable domain and paid API routes are
  absent. Errors use fixed codes without raw exception details.
- Client requests time out after eight seconds. Network/429/503 errors clear stale
  results and retry after 1.5 seconds, stopping after three consecutive failures.

The server uses a disposable source-linked catalog exported earlier from the
recorded v5 references. Atlas remains canonical; no replacement database is added.
The existing catalog expires on **2026-09-26 at 12:01 UTC** and must be refreshed
and redeployed. This change does not assert fresh Atlas access or change v5 artifacts.

## Run and deploy

```bash
.venv/bin/pip install -e '.[dev,camera-server]'
# Use the already prepared model and catalog, or prepare them per document 16.
.venv/bin/uvicorn app.modules.live_camera.server:app --port 8003 --no-access-log
.venv/bin/python -m scripts.prepare_camera_server
```

The staging script writes `.local/live-camera/cloud-run-server/` with only the
camera host, selected frontend files, private model/catalog and licenses. It pins
the installed Python runtime package versions and excludes credentials, certificates,
other application routes and obsolete browser inference assets.

Google Cloud Run uses project `visitadvisor-joan-20260920`, service `visitadvisor`,
region `europe-west4`, one CPU, 1 GiB memory, zero minimum instances, a maximum of
one instance, request concurrency four and request timeout 30 seconds. Runtime
identity `camera-host` has no project roles. Builds use the existing `camera-builder`
identity. A preview revision is verified before switching the stable URL:

https://visitadvisor-485910076824.europe-west4.run.app

Native inference has no per-request model-provider fee. Cloud Run charges for
CPU, memory, requests and applicable transfer/storage/build usage. For a rough
compute-only estimate, at the published request-based rates, one continuously
active 1-CPU/1-GiB instance is about $0.0954/hour before free allowances, requests,
startup boosts and other charges. At two frames per second, request charges add
about $0.00288/hour before free allowances. Actual activity and upload time affect
billing; these are illustrative estimates, not a measured bill or spending cap.
Source: [Google Cloud Run pricing](https://cloud.google.com/run/pricing).

## Validation

The public preview replay used six previously inspected photos, each fetched under
its own URL and cropped/JPEG-encoded in Chromium and WebKit. Both ranked the expected
object first for 4/6 photos. One photo per browser passed the provisional display
floor and margin, correctly; these are substantial remaining recognition limits.
Maximum round trips were 405 ms in Chromium and 1,036 ms in WebKit. Native inference
was 212–242 ms and uploads were at most about 28 KB. These small regression results
are not held-out accuracy, physical-phone measurements or a latency SLA. See
[the replay artifact](server-camera-smoke.json). Desktop browser differences and
JPEG compression mean exact on-device score parity is not claimed.

Tests cover oversize/chunked uploads, invalid JPEG dimensions, wrong location and
snapshot, expiry, sanitization, throttling/concurrency, frame-upload lifecycle,
network recovery, candidate jitter and absence of browser model requests.

Revision `visitadvisor-00006-yer` now receives 100% of traffic at the stable URL.
A post-switch check confirmed HTTP 200 recognition, blocked model downloads and
blank-frame scores below the display floor (375 ms round trip from this computer).
Final checks: 251 Python tests passed, one opt-in Atlas test skipped; seven
JavaScript unit tests and 16 browser tests passed; Ruff lint/format and mypy passed.
Physical-phone and actual mobile-network performance still require a user trial.

## Phase 5 accuracy iteration — 20 September 2026

The server now checks planar detail correspondence before returning a nonzero
candidate score. It reuses the pilot's SIFT ratio test (0.7), distinct reference
matches and RANSAC homography (4 reference pixels, at least eight inliers).
A geometric score of at least 0.4 is required; the DINO score, display floor 0.55,
margin 0.04 and three stable frames remain unchanged. This is an uncalibrated
painting-only experiment; automatic identification stays disabled. Reproductions
of the same composition can also match, so this cannot authenticate an original.

The new [reference manifest](server-camera-reference-manifest.json) preserves the
four earlier sources and adds the public-domain full Milkmaid composition. The
old bundle had only a fragment and a gallery view for that painting. Adding the
full reference alone did not improve the six historical cases. The local feature
vectors are derived evidence kept in the private server bundle, never sent to the
browser; frame dimensions, upload limits and memory-only processing are unchanged.
The original v5 manifests and first server smoke report remain frozen.

The [accuracy replay](server-camera-accuracy-v1.json) records a Pillow simulation
of the browser's central 87.5%-of-short-edge crop, resized to 224 square and encoded
as JPEG quality 90. Correct suggestions on the six historical positive labels
increased from 1/6 to 3/6; incorrect suggestions on 84 other evaluation images
fell from 9/84 to 0/84. Three separately sourced Milkmaid photos also received the
correct suggestion. These photos had already appeared in a source audit: they
are not an unseen release holdout. Maximum local inference was 221.41 ms.

Label audit: the Job Koelewijn image is an installation/reproduction; the
HA-0028849 central crop is dominated by visitors; the WGA24626 detail is a
canonical reproduction, not an independent visitor capture. The replay retains
all six original labels for an honest before/after comparison. It must not be
reported as six clean, independent real-artwork trials. No thresholds were
lowered to force those images to pass. Phase 5's broader accuracy gate remains
open, and Phase 6 has not started.

Required checks passed: 256 Python tests, one opt-in Atlas test skipped, Ruff
lint/format and mypy. A synthetic native-feature test verifies that two equally
high embedding scores separate correctly when only one has matching geometry.

Cloud preview verification in both Chromium and WebKit produced 3/6 suggestions,
all correct under the unchanged floor/margin, compared with 1/6 previously.
See [browser replay](server-camera-accuracy-v1-browser.json). Maximum round trips
were 1,679 ms and 1,173 ms respectively; first Chromium inference took 1,501 ms,
so cold-path latency remains a limitation despite faster subsequent requests.
Seven JavaScript unit tests and all 16 browser lifecycle tests also passed.
The public catalog hash matched the staged bundle, local features were absent
from public metadata, and model downloads returned 404.

Revision `visitadvisor-00008-hib` replaces `visitadvisor-00006-yer` at the stable
URL following successful preview checks. This is a Phase 5 accuracy update,
not completion of the Phase 5 release gate.

## Tracking correction after phone feedback

A rejected frame returns both object scores as zero. Their ID ordering previously
cleared a verified Jewish Bride immediately because The Milkmaid appeared first,
bypassing the existing 1.5-second weak-frame grace period. The tracker now treats
subthreshold or ambiguous candidates as missing evidence, regardless of ordering.
Only a different candidate that passes both floor and margin clears the identity
immediately. Large motion, stopped/stalled video and expired evidence still clear
it. Weak frames never refresh the verification timestamp or establish a match.
The grace duration and server recognition thresholds are unchanged.

Regression coverage includes zero-score ordering, an ambiguous competing identity,
recovery without hiding the card, expiry under repeated rejected frames, and
reacquisition requiring three strong frames. Physical-phone smoothness still needs
confirmation; this is a bounded correction to the tracking logic.
