# Live-camera prototype — 2026-09-19

> **Superseded camera architecture — 2026-09-20:** The user authorized moving
> recognition to the server with temporary frame uploads and no phone model
> download. See [the server camera trial](17-server-camera-trial.md). On-device
> statements below describe the earlier prototype.

The user requested continuous object recognition as an iPhone 12 Pro camera moves
between objects, with no shutter button, gallery upload, or transmitted camera
imagery. The reported target is iOS 26.6.1 / Safari. Browser capabilities are checked
at runtime; the device itself has not been tested from this development environment.
This is an authorized extension to the previous Phase 5 scope. Phase 5 remains
incomplete, and automatic identification in the existing API remains disabled.

## Implemented experience

- Tap Start once to grant rear-camera access; audio is never requested.
- Keep one object in the center guide. The live preview continues while selected
  frames are analyzed locally in a dedicated worker.
- Display a **possible match** only after three consecutive sufficiently separated
  candidates. Scores and temporal agreement are not calibrated confidence.
- Clear results when the candidate changes, the scene changes substantially,
  the video stalls, or strong results become older than three seconds. Once a
  candidate is established, tolerate small hand movements and retain it for up to
  1.5 seconds after the last strong result during a weak view, labeled as rechecking.
  Weak results never extend this grace period or establish a new match.
- Stop tracks and terminate the model worker on Stop, page exit, or backgrounding.
  Returning requires a new Start tap. Late permission grants are stopped too.
- Show the supported-object list, canonical source links, reference date, local
  frame-analysis time, rolling p95 (last 300 frames), and frame count.

This first prototype matches the **center region to one known identity at a time**.
It does not locate and track bounding boxes around multiple objects, recognize
arbitrary objects, use GPS, record video, or produce generated stories. Motion
detection is a simple image-difference heuristic and can miss slow scene changes;
fresh matching and the expiry timer provide additional clearing, not identity proof.
The movement threshold remains 0.09 during acquisition and is 0.22 while retaining
an established candidate. These are uncalibrated display heuristics; acquisition
scores, margins and the requirement for three agreeing frames remain unchanged.

## Local recognition

Use the existing pinned `facebook/dinov2-small` weights at revision
`ed25f3a31f01632728cabb09d1542f84ab7b0056`. The export produces a fixed
`[1,3,224,224]` input and normalized 384-dimensional CLS embedding at ONNX opset 17.
The float32 model is about **84.3 MiB**, plus the ONNX runtime download. The first
prototype deliberately measures this baseline before mobile quantization work.

`onnxruntime-web` is pinned to 1.30.0 in the npm lockfile. Its JavaScript and WASM
files are copied locally. Runtime uses one WASM thread in a dedicated module worker;
WebGPU availability is reported but does not select an untested GPU path. No CDN,
model provider, API key, or per-frame network inference is involved.

Canvas uses center-crop geometry equivalent to shortest-edge 256 then crop 224,
with RGB ImageNet normalization. Canvas interpolation is **not identical to Pillow
bicubic**. The new `canvas-center-shortedge256-crop224-rgb-imagenet-v1` identity is
recorded separately. Existing reference descriptors retain their original model
and preprocessing identity. Numerical ONNX tensor parity does not establish camera
pixel preprocessing parity, recognition accuracy, or iPhone performance.

Ranking combines 75% full-reference similarity with 25% best regional similarity
**within each reference view**, then selects the best view per object. The demo's
0.55 candidate floor, 0.04 runner-up margin, and three-frame agreement are explicitly
uncalibrated preview heuristics. The failed v5 automatic policy is not enabled.
There is only one inference in flight, no frame queue, and at least 100 ms between
analyses (target interval 500 ms when processing permits). An analysis taking more
than three seconds cannot promote a candidate. A 15-second inference timeout stops
the session; model download/init has a five-minute timeout. The UI shows download
percentage, model verification and initialization stages before recognition starts.
Verified model weights are saved in browser IndexedDB under their SHA-256
identity and reused across page reloads. Cached bytes are reverified; corrupt entries
are replaced. If storage is unavailable, full or evicted, recognition falls back
to downloading. Only model weights are cached, never camera images or descriptors.
This is not an offline mode: the page, current references and runtime still require
network access. A model update or cleared browser storage requires a new download.
During analysis it shows checked-frame counts and distinguishes an unsupported
view from motion, a stalled camera or analysis taking longer than three seconds.

## Preparing model and references

```bash
.venv/bin/pip install -e '.[dev,visual,camera]'
npm ci
npm run camera:vendor
# Only if pinned weights are not already cached:
.venv/bin/python -m app.modules.visual model
```

The preferred Atlas export accepts 2–12 explicit covered objects from one active
location. It performs read-only async PyMongo operations, checks model/preprocessing,
source membership and evidence hashes, then checks references again before export.
Example with two existing manual identities:

```bash
.venv/bin/python -m app.modules.live_camera.export \
  --location-id 6a9dd4106d52c3bc3965869d \
  --object-id 6a9dd4126d52c3bc396586a0 \
  --object-id 6a9dd4136d52c3bc396586a1
```

Only explicit public canonical fields, source IDs/links, reference versions and
derived vectors are exported. Raw source records, credentials and unreviewed
generated text are excluded. The reference snapshot expires after seven days; it
does not automatically reflect Atlas changes during those seven days. Regenerate
before a field session or after changing sources/references. The worker verifies
the model SHA-256 before loading and rejects wrong-location, malformed, expired or
automatic-identification catalogs.

During development Atlas returned TLS handshake errors, so no claim of a fresh
Atlas export is made. A separate **explicit recorded-source demo** is available:

```bash
.venv/bin/python -m scripts.prepare_camera_demo
```

This reconstructs descriptors for The Milkmaid and The Jewish Bride from the four
previously recorded licensed references in
`visual-validation-v5-reference-manifest.json`. It uses the existing bounded HTTPS
reference downloader, keeps pixels transient, retains the original reference
evidence and image hashes, and creates new `recorded-demo-*` descriptor identities.
It never writes to Atlas or modifies frozen v5 artifacts. Catalog and UI explicitly
mark Atlas freshness unverified. This is demo data, not a replacement evaluation
set or an assertion that these are the current Atlas descriptors.

Model, catalog, runtime binaries, certificates, and local browser reports are
ignored under `.local/live-camera/`. Model/runtime license texts are shipped with
the frontend and copied into the download bundle. No user camera frames are saved
there. The filesystem-hosted bundle is a disposable development artifact; Atlas
remains the canonical project store.

## Running and opening on iPhone

For a computer-only preview:

```bash
.venv/bin/uvicorn app.modules.live_camera.server:app \
  --host 127.0.0.1 --port 8002 --no-access-log
```

Open `http://localhost:8002/`. The normal Atlas-backed application also mounts the
same UI at `/live/`; its existing startup requirements still apply. For phone tests,
use the **standalone** application, which exposes no database writes, paid API
routes or upload endpoint. Only GET/HEAD are accepted. Bundle filenames are
allowlisted. CSP restricts scripts, workers and network connections to the same
origin; microphone/geolocation are disabled. Camera pixels and descriptors only
travel between the main page and its local worker. Statistics stay in page memory.

Safari requires a trusted secure context. Ordinary `http://LAN-IP` cannot access
the camera, and merely bypassing a certificate warning is not a reliable substitute.
For a same-Wi-Fi test, substitute the computer's private LAN IPv4 address:

```bash
.venv/bin/python -m scripts.camera_https --ip YOUR_LAN_IP
# Terminal 1: read-only certificate/setup page
.venv/bin/uvicorn app.modules.live_camera.server:app \
  --host 0.0.0.0 --port 8002 --no-access-log
# Terminal 2: read-only HTTPS camera host
.venv/bin/uvicorn app.modules.live_camera.server:app \
  --host 0.0.0.0 --port 8443 --no-access-log \
  --ssl-certfile .local/live-camera/tls/server.pem \
  --ssl-keyfile .local/live-camera/tls/server.key
```

On the iPhone open `http://YOUR_LAN_IP:8002/phone-setup.html`, download the local
certificate, install its profile, and enable its trust in Settings → General →
About → Certificate Trust Settings. Then open `https://YOUR_LAN_IP:8443/` in Safari.
The setup page has the link. The certificate is named **VisitAdvisor local camera
trial**, expires in seven days, and should be removed from the phone after testing.
Its signing key is deleted after the server certificate is issued; the server key
remains private and cannot be served by the application. The helper refuses to
overwrite existing certificates. If the LAN IP changes, generate a new certificate
and repeat trust setup. Device certificate installation is a user action, not
something the development environment can complete remotely.

Reference: [Apple certificate trust instructions](https://support.apple.com/102390).
The certificate setup is specific to the standalone root URL; use an existing
trusted HTTPS host for other deployments. No public tunnel or cloud deployment is
created by these scripts. The computer must remain running and reachable from the
phone; guest Wi-Fi isolation or a host firewall may prevent access.

## Verification and remaining work

The initial export measured a maximum normalized-embedding difference of
`5.07e-7` between Torch and native ONNX Runtime on three fixed test tensors. Actual
ONNX inference has also run in Chromium and Playwright WebKit on the development
computer. This is not a physical iPhone result.

A [six-photo browser replay](live-camera-browser-smoke.json) against this two-object
demo ranked the correct object first for **4/6** previously inspected photos in
both browsers. Only **2/6** passed the provisional candidate floor and margin, both
correct; the two wrong top choices remained below the floor. No negatives
were included, and temporal agreement was not measured by this static replay.
Inference took roughly 0.33–0.40 seconds on the development computer. These results
show that camera testing can begin, but also expose substantial recognition gaps;
they are not new held-out validation or evidence for automatic identification.

Final development checks: **237 Python tests passed, one opt-in live Atlas test
skipped; five JavaScript tests and ten browser tests passed**. Ruff lint/format and
`mypy app` passed. Chromium and WebKit browser tests include actual local ONNX
inference. The existing two Python dependency deprecation warnings remain.

Behavioral tests cover candidate changes, unknowns, temporal stability, lifecycle
cleanup, permission denial, late camera grants, missing bundles, invalid vectors,
location isolation and source freshness. Browser network assertions check that
inference uses only same-origin GET downloads with no request bodies. The standalone
server rejects writes and does not expose non-allowlisted bundle files.

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
npm test
npx playwright install --with-deps chromium webkit
npm run test:browser
```

Physical trial still needs: actual iPhone camera access, download time, sustained
frame-analysis p95, time to a stable correct candidate, motion/unknown rejection,
memory stability, battery and thermal behavior, and recovery after Safari is
backgrounded. A screen/print of an artwork is useful for checking the interface
but is not independent museum recognition evidence. Broader coverage and the
existing held-out recognition gates remain required before automatic identity
claims. Multi-object detection and tracking would be a later bounded iteration.

Technical sources:
[ONNX Runtime browser deployment](https://onnxruntime.ai/docs/tutorials/web/deploy.html),
[worker/thread configuration](https://onnxruntime.ai/docs/tutorials/web/env-flags-and-session-options.html),
[camera secure-context requirements](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).

## Google Cloud trial — 2026-09-20

The user authorized public hosting of the standalone camera demo. The Cloud Run
service is `visitadvisor` in project `visitadvisor-joan-20260920`, region
`europe-west4`. It uses the two-object recorded-source demo, with references
expiring on **2026-09-26 at 12:01 UTC**. Refresh the source-backed demo bundle and
redeploy before that expiry; hosting does not refresh references automatically.

The deployment package is staged under the ignored
`.local/live-camera/cloud-run/` directory. It includes only the standalone host,
frontend, model, runtime, licenses and catalog; it excludes credentials, local
certificates, Atlas access and the writable API. The deployed location menu lists
only the packaged Rijksmuseum catalog. Camera processing still happens on-device.

The trial uses 512 MiB server memory, one CPU, zero minimum instances and a maximum
of one instance. The runtime service account has no project roles. Build access
uses a separate service account. Hosting, build, storage and download usage may
incur charges on the selected billing account.

The model download uses streaming HTTP responses because it exceeds Cloud Run's
[32 MiB non-streamed response limit](https://docs.cloud.google.com/run/quotas).
Model integrity verification and catalog expiry checks remain enabled.

Public trial URL: https://visitadvisor-485910076824.europe-west4.run.app
Revision `visitadvisor-00002-q7p` passed public HTTPS, read-only boundary, and
actual model download/inference checks in desktop Chromium and WebKit. Physical
iPhone behavior remains unverified. The existing checks also passed: 237 Python
tests (one opt-in Atlas test skipped), five JavaScript tests, Ruff and mypy.
