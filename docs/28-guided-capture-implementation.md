# Admin guided reference capture: implementation

Implemented on `feat/guided-reference-capture`, following the design in
[27](27-guided-reference-capture.md). Commit and publication were authorized on
2026-11-06. Publishing uses the existing VisitAdvisor service and preserves its
runtime configuration and recognition model assets. Implementation and automated
checks did not create production photo or catalog records.

## Save and embed a collection, 2026-11-08

The camera now includes object name and type alongside the selected photos.
**Save** creates the object, uploads every selected photo and embeds/publishes the
collection before reporting success. There is no per-photo OK/Done or separate
Prepare action. Missing name/type can still be completed in the details form;
native-camera and gallery selections use that same save flow. The single-photo
form for an existing object also embeds automatically after upload.

The authenticated `POST /api/v1/admin/venues/{location_id}/objects/{object_id}/save`
uses the existing cached local DINO encoder, foreground lock and atomic reference
set publisher. Existing views are preserved and only pending originals are
encoded. A repeated successful save returns the existing compatible set without
encoding again. The legacy `/prepare` endpoint remains for existing API clients,
with its previous behavior, but the admin page does not call it.

An upload or embedding failure keeps the created object and completed uploads.
**Retry save** reconciles source hashes/session IDs before uploading anything
missing, then retries publication; an existing object's pending uploads expose
**Save photos** for the same recovery. Failure leaves the previous reference set
active. Uploads and publication are separate operations, so interrupted saves can
leave private pending photos until retried. This changes the admin workflow;
it adds no training, external model provider, worker queue or recognition claims.
The static release URLs use `save-20261108-1` with explicit revalidation.

## Quick photo capture, 2026-11-07 (save flow updated above)

The operator requested a fast capture flow after trying the server-assisted
review below. The current `quick-capture.mjs` opens the camera directly from
**Add object**. **Take photo** immediately keeps a source JPEG and returns to the
live camera; up to four photos can be taken without waiting for inference.
**Save** completes the collection using the save flow above. There is no mandatory
profile selection, per-photo confirmation, Next view step or angle choice.
Optional photo details can record an explicitly confirmed angle; unknown angles
remain unverified and publish as null.

For a walk-around object, selected photos enter a bounded, sequential analysis
queue. The center of the frame is the default prompt; an optional live tap can
move it. Candidate zero is recorded as an unreviewed suggestion, not selected by
its IoU score or certified as a complete object. The original photo remains the
descriptor input. Flat artwork and room/scene profiles keep the full frame by
default because the model can isolate a depicted subject instead of the whole
artwork. A thumbnail inspector offers optional correction and removal. Quality
measurements are advisory, cover the full frame and never block the shutter.

Analysis can finish while the operator enters a name. **Save** stops outstanding
analysis, marks unfinished observations skipped, and uploads the sources without
waiting. Late responses cannot change saved metadata, another object, venue or
capture session. Model failure retains the photo. Native-camera/gallery fallbacks
also retain selected sources. Only shutter-selected JPEGs are sent to analysis;
there is no continuous video upload. Each JPEG has a maximum 1600-pixel edge and
1 MiB, and the analysis and later photo upload use the same bytes.

`guided-capture-v3` distinguishes pending, suggested, unavailable and skipped
observations. Source and derivative hashes are validated before storage;
automatic suggestions carry pinned model identity and explicitly record
`reviewed: false`. Earlier v2 clients still require explicit review. Binary masks
remain separate private derivatives; capture never fabricates viewpoint coverage.
The current HTML, script, stylesheet and module share `quick-20261107-1` release
URLs and preserve explicit static revalidation from the cache correction below.

When enabled, the application prepares the CPU model during startup, including
one synthetic blank-image forward pass. Its weights then stay resident for the
instance lifetime; image data, prompts and embeddings do not. The existing
45-second startup and 20-second inference bounds, shared single-child lock,
cancellation and safe fallback remain. No new service, external model provider,
GPU or minimum instance count is introduced. This removes repeated cold loading
from ordinary analysis, while the shutter/name/save flow also works when analysis
is slow or unavailable.

If Save disconnects an already submitted still, its bounded inference finishes
under the same lock and the result is discarded. This preserves resident weights
for the next object without accepting another server request concurrently. Real
timeouts, invalid worker replies, task cancellation and shutdown terminate the
child. Background retries handle this short busy interval without blocking the
camera or save form.

On one local CPU, the first real request improved from 5.70 to 3.21 seconds after
startup preparation. With DINO and ONNX already warm, three real requests took
3.82–4.04 seconds, and sampled combined RSS peaked at 1,757,088 KiB. Repeated
same-source candidate masks, scores and boxes were byte-for-byte unchanged.
These measurements show capacity and loading behavior, not detection accuracy or
physical iPhone throughput.

The hosted preview returned three source-linked candidates in real Chromium and
iPhone-profile WebKit requests. Shutter-to-thumbnail times were 87/111 ms and
Done-to-name-form times 82/265 ms, while analysis completed in about 10.9/7.9
seconds. An actual HTTP disconnect/reuse check returned the next photo's three
candidates after one busy retry, with 8.45 seconds of model inference and no cold
reload. The service still scales to zero: its cold application startup includes
model preparation and can delay the initial page load. Hosted preparation took
about 30 seconds after capture model startup; total application startup was about
57 seconds. No minimum instance count or extra always-on resources were added.

Revision `visitadvisor-api-quick-3e4399c30a3d` was promoted to 100% production
traffic on 2026-11-07. The immutable image is
`sha256:107e20c924967625274697d1ba08c240a66c271feaedd811f5ee09935a4168ca`;
runtime configuration matches the preceding revision apart from that image.
Local checks passed 497 Python tests (two skipped), Ruff lint/format, `mypy app`
and 33 Node tests. The browser matrix passed 90 checks plus 21 focused regressions.
Hosted checks verified health/database, source asset hashes and cache headers,
real-model Chromium/iPhone-profile requests and HTTP cancellation/reuse without
creating production catalog records. A repeated hosted fixture run passed 22 of
24 cases; two desktop WebKit synthetic-camera starts delivered no frames despite
an active video track and reached the existing retry/native-camera fallback.
Those cases had no JavaScript errors and remain unresolved in this test
environment; this is not physical-device acceptance. Separate repeated opening
checks passed eight times. The prior startup revision remains available for
rollback.

## Server-assisted capture update, 2026-11-07

The operator authorized selected stills reaching our backend after reporting
incomplete masks and repeated sharpness/target warnings on iPhone 12 Chrome.
The following earlier flow superseded the automatic browser capture described
below and has since been replaced by Quick photo capture above.
It does not establish a measured segmentation accuracy percentage.

The earlier `server-guided-capture.mjs` uses the browser camera without requiring
worker WebGL. A target tap freezes one metadata-free JPEG (maximum 1600-pixel
edge and 1 MiB) and sends it to the authenticated, same-origin
`POST /api/v1/admin/capture/analyze`. It uploads neither continuous video nor
unselected preview frames. No external inference provider receives the images.
The frozen original is retained unchanged for a possible subsequent photo upload.

The server runs pinned `facebook/sam2.1-hiera-tiny`, revision
`de431c4043854a71d8101e17995dfe596bf101a5`, locally on CPU. Model/config files
are hash checked during explicit preparation and local loading. The checkpoint
is 155,908,064 bytes, SHA256
`48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7`.
Runtime model-hub access and telemetry are disabled. Image bytes, embeddings and
prompts are transient; the service stores only bounded session counters, with no
analysis database writes or image-body logs.

The operator sees up to three candidate masks with no automatic winner. They can
draw a rectangle around the whole target and request another analysis, choose
that rectangle directly for a flat artwork, or keep the full original. The
whole-object confirmation is required before keeping a photo. Sharpness and
exposure are advisory during this review: low texture is not reliable evidence
of blur. No missing object pixels or synthetic angles are generated. Actual
photo angles still need separate explicit confirmation on the name form.

Analysis allows one request at a time per API process, up to twelve requests per
five-minute session and at most 32 tracked sessions. Requests have a bounded body
read (five seconds), model-start deadline (forty-five seconds) and inference
deadline (twenty seconds after the model is ready).
A separate local child makes model work cancellable;
timeout/disconnect terminates it instead of leaving inference running behind an
unlocked gate. The child unloads after sixty idle seconds. A changed target,
closed dialog, hidden page or canceled import
invalidates late results. Native camera and gallery options remain available,
including after analysis failure, and preserve already selected photos.

`guided-capture-v2` adds explicit selection method, operator review, exact source
SHA256 and mask SHA256. A selected server mask records its candidate and pinned
model identity; a manual rectangle never claims to be a model mask. An attempted
model analysis is recorded separately from the final selection. Full-frame
selection has no mask. Upload rejects source/mask hash mismatches. Existing v1
metadata remains accepted for older clients. Both versions keep descriptors on
the original photograph; masks remain separate private derivatives.

Prepare the model explicitly after installing the optional capture dependencies:

```sh
pip install --extra-index-url https://download.pytorch.org/whl/cpu \
  -c constraints-visual.txt '.[camera-server,visual,capture-server]'
python -m scripts.prepare_server_capture
python -m scripts.prepare_server_capture --check
```

The API feature is opt-in through `ADMIN_CAPTURE_SERVER_ENABLED=true` and
`ADMIN_CAPTURE_MODEL_DIRECTORY` (default `.local/server-capture`).
For a fresh Docker image, use `EXTRAS=camera-server,visual,capture-server`,
`CONSTRAINTS=constraints-visual.txt`, the CPU torch index and
`PREPARE_SERVER_CAPTURE=1`; the prepared directory is `/app/server-capture`.
Deployments must measure the combined camera/admin/model memory before enabling
the feature. Physical iPhone and other-device acceptance remains required;
browser emulation is regression coverage rather than real-device proof.

The [integration check](guided-capture-server-integration-2026-11-07.json) ran the
real worker with the DINO admin encoder and ONNX visitor camera already warmed,
restricted to one CPU core. Three JPEGs returned three masks each; cold latency
was about six seconds and warm latency about four seconds. Sampled combined RSS
peaked at 1,741,452 KiB. This is a capacity check, not mask accuracy validation.
Actual same-origin browser-to-API checks returned candidates and retained review
photos in Chromium and an iPhone WebKit profile without a segmentation worker.
The automated checks passed 75 browser cases across three profiles, with three
optional legacy-model checks skipped. Required Python/static checks also passed.

Revision `visitadvisor-api-server-b033d7e2a76d` was deployed through the
`server-capture` preview tag, then promoted to 100% production traffic on
2026-11-07. Preview health/database, exact asset hashes, four hosted browser
regressions and real-model Chromium/iPhone-profile requests passed before
promotion. The service keeps one CPU and one maximum instance; memory increases
from 2 GiB to 3 GiB for the measured model workload. Existing secrets, recognition
assets and runtime settings are preserved except the two explicit capture
settings. Previous revision `visitadvisor-api-target-57b7c003281f` remains
available for rollback. Validation did not save production objects or photos.
Local final checks passed 469 Python tests (two skipped), Ruff lint/format,
`mypy app`, 33 Node tests and the browser cases above.

### Camera startup cache correction

The operator subsequently reported WebKit's `null is not an object` while
evaluating `canvas.getContext`. The exact failure was reproduced in WebKit and
an iPhone 12 profile by loading the pre-server-capture `admin.js` with current
HTML. That older script imports `guided-capture.mjs`, whose startup accesses the
removed `guidedOverlay` canvas. Prior static responses supplied validators but
no explicit cache policy, and the page reused unversioned script URLs.

The page now versions its admin script, stylesheet, capture module and quality
module together (`camera-20261107-2`). Admin HTML and static files explicitly
revalidate on every request, including 304 responses. Pinned model assets keep
their existing immutable-artifact caching. Regression checks simulate cached
unversioned scripts and confirm that opening the camera uses the new module in
Chromium, WebKit and the iPhone profile. Selected photos are never discarded to
force an automatic page refresh; an already open older page needs navigation to
the updated page.

Revision `visitadvisor-api-cache-811449053036` was promoted to production after
cache-policy/hash/health checks and hosted camera regressions. The exact original
error was reproduced in two WebKit profiles before the fix; six repeated hosted
WebKit opening checks passed afterward. Production serves explicit revalidation
headers and the versioned entrypoint. Validation created no catalog records.

### Cold model startup follow-up

Hosted checking also reproduced an independent timeout on the unchanged
production model service: the old twenty-second limit combined cold process
startup with image inference, killed the child, and made each retry cold again.
Startup now has a separate forty-five-second bound; image inference retains its
twenty-second bound after an explicit worker-ready message. The browser allows
eighty seconds and explains that the first analysis can take longer. Manual
rectangle/full-photo choices and cancellation stay available throughout.

The hosted preview measured 6.56 seconds for spawn/artifact verification, 17.46
seconds for dependency imports and 0.30 seconds for model initialization. The
first request succeeded in 35.6 seconds with 10.5 seconds of inference; the next
succeeded in 8.1 seconds. Fixed phase messages contain no photo, prompt or
credential data. Loading/inference still run in one cancellable child, with no
provider call, queue or new service. A regression verifies that startup does not
consume the inference budget, and checks passed 477 Python tests (two skipped),
Ruff lint/format, mypy, 33 Node tests and the scoped browser cases.

Revision `visitadvisor-api-startup-2b90c835edbe` was then promoted to 100%
production traffic after hosted health/hash/cache checks, cold/warm actual-model
requests and iPhone-profile cache/opening regressions passed. Production returned
three candidates with the exact submitted source hash in 31.0 seconds; health and
database checks passed. No production catalog records were created by validation.

## Earlier browser-only admin flow

1. Choose a venue and **Add object**. Guided capture opens directly; the previous
   photo-selection page and its extra Next step have been removed.
2. Choose flat artwork, walk-around object, front-only display, or building/room.
   This is an admin choice, not automatic semantic classification. Existing object
   type can suggest a default.
3. Start the camera and tap the target. A real interactive segmenter isolates
   visible target pixels. Its last analyzed frame appears separately from the
   live preview so a delayed mask cannot appear to track current video.
4. Target-only focus, exposure, size and perceptual novelty decide whether to
   retain or improve a candidate. At most one photo per requested prompt and four
   overall are retained. Move to the next position and tap the same object again
   to confirm it from that side.
5. Review original/isolated-target previews and remove incorrect photos. Camera
   stops when review opens. An incomplete set can be used truthfully.
6. **Use selected photos** opens the name form directly, with the selected photos
   and their actual-angle controls on that form. Enter the name, existing object
   type and actual photo angles, then save. Suggested prompts never pre-fill
   confirmed angles. Save uses the existing authenticated upload/retry handling.
7. **Prepare references** remains a separate action in object details.

No photographer, creator or permission-detail fields/checkboxes were added.
Gallery and native-camera fallbacks are inside guided setup, including after
permission/model failures. They also continue directly to the name form.
Canceling a fresh guided capture returns to the collection. **Add more photos**
reopens guidance from the name form; canceling it keeps existing photos/name.
Pending upload retries resume the name form without opening the camera.

## Runtime and bounds

The worker imports pinned MediaPipe assets from `/admin/capture-assets/`.
The selected runtime is `@mediapipe/tasks-vision` 1.2.2 with
`InteractiveSegmenterLegacy` and the pinned MagicTouch float32 version 1 model
(6,227,884 bytes; SHA256
`e24338a717c1b7ad8d159666677ef400babb7f33b8ad60c4d96db4ecf694cd25`).
Preparation verifies runtime/model hashes. Browser sessions call no external AI
provider. SDK automatic telemetry is blocked before import; the production
worker response also restricts connections to the same origin using CSP. Public
model assets contain no admin credentials or photos.

Each target tap now snapshots the visible frame immediately. A tap during an
in-flight analysis replaces the pending selection; late results cannot overwrite
the newer target. Once a clear photo is retained, analysis pauses for that view,
keeping its outline, success message and Next view control stable during movement.
Tap again to retry the current view, or choose Next view and tap the object from
the new position. Duplicate-view rejection explicitly reports that the object was
detected but the view is too similar. These interaction fixes do not establish
real-phone segmentation accuracy. A stalled analysis still offers camera retry
when a newer tap has superseded its target selection.

Preview and worker inference run independently. Analysis uses one request in
flight and drops intermediate video. Automatic retries wait at least 650 ms
between completed analyses; an explicit tap can start immediately when idle.
It promises no fixed frame rate. Analysis
images have a maximum 640-pixel edge; selected JPEGs have a maximum 1600-pixel
edge. Only selected candidates retain encoded JPEG/mask/preview blobs, with at
most four candidates. Video and unselected frames are not recorded.

Template correlation checks local appearance continuity between segmentations.
Weak texture, lost appearance, edge clipping and large silhouette changes request
reselection. Each new prompted view requires an explicit target tap. A semantic
person/occlusion detector and automatic object-type classifier are not
implemented. A person already covering the target at the initial tap can still
produce a wrong mask; admins must reject it in review. Missing pixels, synthetic
angles and object details are never generated.

The target-loss follow-up adds a bounded pixel search around the last confirmed
box, covering up to five analysis-image pixels per axis in addition to the coarse
search. Confidence and photo-quality thresholds are unchanged. Two failed
tracking frames retain the selection for another check; no segmentation or photo
selection occurs on those uncertain frames. A third consecutive miss requests
reselection. A quality-rejected frame with an unusable appearance template pauses
automatic retries and keeps its sharpness/lighting guidance and photo fallback
instead of immediately replacing them with “Target uncertain.” Node regressions
cover small translations, replacement and occlusion; browser fixtures cover
transient recovery, persistent loss and weak detail. These are controlled
regressions, not physical-phone recognition validation.

On 2026-11-07, revision `visitadvisor-api-target-57b7c003281f` was promoted to
100% production traffic after preview health/database, deployed-asset hashes and
three capture-regression browser checks passed. It includes the browser fallback
and target-loss fixes above. Runtime settings and model assets were preserved.
Local verification passed 442 Python tests (two skipped), all required static
checks, 31 Node tests and 16 scoped Chromium/WebKit/iPhone-profile checks (two
profile-specific checks skipped).

Close, Escape, page hiding/exit, logout and late permission results stop camera
tracks and terminate the worker. Session epochs invalidate late frames/imports.
Timeouts terminate stalled workers. Missing capabilities/model failures leave
photo upload available. Canceled imports cannot change/close a newly opened
capture session.

## Source evidence and masks

Selected source JPEGs remain private in `admin_reference_photos`, with their
original upload hash. They are optimized, metadata-free camera frames, not raw
sensor files or full video resolution. Binary foreground masks remain separate
derivatives and never overwrite the source.

Upload accepts optional `capture_metadata` JSON (32 KiB) and `foreground_mask` PNG
(512 KiB). The strict schema includes session/frame IDs, profile, requested view,
admin-confirmed angle flag, normalized target box, quality and segmenter identity
and hash. Masks must be opaque, grayscale, binary, within 16 megapixels and match
the normalized source dimensions. The server strips mask metadata and stores
canonical bytes/hash/dimensions. Masks are served only through the authenticated,
no-store admin photo route.

`viewpoint_verified` defaults false and becomes true only after an explicit angle
choice. Until then, publication sets viewpoint to null. Mask hashes and capture
observations remain linked to the source upload in reference evidence.

Descriptor preparation still uses the selected original. Crop/mask descriptors
have not replaced it: that change needs the independent recognition comparison
in doc 27. Masks currently support selection/inspection and the future experiment.

## Local setup

```powershell
npm ci
python -m scripts.prepare_guided_capture
python -m scripts.prepare_guided_capture --check
python -m app.main
```

Only preparation downloads pinned public artifacts. It can obtain the verified
npm tarball in Python-only environments, and accepts `--model-file` for an offline
copy. Assets stay under ignored `.local/guided-capture`; they are not committed.
Docker runs the same hash-checked preparation at image build time. No model
download occurs on API startup.

Use HTTPS on phones or localhost on desktop. Actual Safari/iPhone and
WhatsApp/Telegram embedded-browser camera behavior needs device testing;
desktop Chromium passing does not prove those environments.

The supported experience includes a native-camera/gallery fallback. These
controls remain available during live capture, including target-isolation
failures, and retain earlier selected photos when switching methods or canceling
the file picker. Input-picker cancellation is handled separately from closing
the capture dialog. Segmentation errors request target reselection rather than
claiming the model is unavailable.

Worker initialization checks for a usable OffscreenCanvas WebGL context before
loading assets: the pinned CPU model still uses textures for image input.
Linux WebKit with an iPhone 12 touch profile exposed worker 2D canvas but no
worker WebGL; the old runtime loaded and then failed on every segmentation.
That environment now falls back early. This is a browser-capability result,
not proof of the capabilities of a physical iPhone 12. Real phone testing and
Firefox verification remain pending. The added portrait/touch regression uses
mocked camera/model results; it verifies interaction and fallback preservation.
The expanded local checks pass 85 admin browser tests across Chromium, WebKit
and an iPhone 12 device profile (five opt-in/profile-specific checks skipped),
plus 29 Node tests. A separate real-model Chromium smoke passes against the
local FastAPI asset and worker routes. WebKit upload assertions inspect the
browser's submitted FormData because Playwright's WebKit interception omits file
bytes; a temporary HTTP receiver confirmed that the real request carries them.

## Verification and remaining acceptance gates

Coverage includes metadata/mask validation, access control, source hashes,
unverified viewpoint exclusion, target-only quality, JPEG/background duplicate
rejection, bounded selection and appearance tracking. Browser fixtures cover
capture/review/upload, explicit angle choice, permission/model failures, late
callbacks, duplicate launches, cancellation during hashing and page cleanup.

The real model was also exercised on synthetic and historical local photos,
separately from mocked UI tests. These are runtime/segmentation smoke tests,
not independent visitor queries or proof of recognition accuracy.

On this Windows/Intel Iris Xe desktop in Chrome, legacy CPU inference on a
512-by-384 historical painting photo took about 114-117 ms warm, compared with
about 7.7 seconds for the newer v2 CPU model. Synthetic legacy results took about
113-118 ms warm. The v2 GPU trial was faster but produced masks excluding the
selected point, so it was rejected. These measurements exclude camera acquisition,
download, quality checks and uploads, and do not establish phone throughput.
An ornate sculpture sanity check produced an incomplete mask: explicit mask
review and native-photo fallback are necessary. Segmentation does not certify
complete object visibility.

The full Linux validation run passed 442 Python tests (two opt-in tests skipped),
Ruff lint/format, and `mypy app` (Cloud Build
`e253cbbc-6169-449c-bf83-0f1d7bdfa877`). The local Windows full-suite command still
hits the existing four Unix `resource` import collection errors; native Windows
mypy reports the same module's two platform attributes. The Linux run covers
the full suite. Local scoped backend tests and Linux-platform mypy also passed.
Final local verification also passed 28 Node unit tests and 25 Chromium admin
browser tests; the optional real-model test is separate. The real model passed
against actual FastAPI worker/CSP/asset routes: three valid synthetic masks,
clean disposal and zero external requests. Latest frame times were about
217/130/146 ms. All 83 scoped Python capture/admin/asset tests passed after the
final model change, as did Ruff and Linux-platform mypy.

Reproduce unit/admin tests with `npm test` and `npm run test:admin` (requires
Playwright browser installation). Opt in to the real model smoke test with
`RUN_GUIDED_MODEL=1`; set `CAPTURE_SMOKE_URL` to the running application's
`http://127.0.0.1:8001/admin/` URL to exercise its actual CSP and asset routes.
Without that URL, the test uses the admin suite's static server and routes
the locally prepared model assets through the same test origin.

Next acceptance work: real captures on target phones; mask review on glass,
shiny/thin/smooth objects, similar neighbors and foreground/background people;
then independently captured visitor queries under intended distance, viewpoint
and lighting conditions. Quality thresholds remain experimental. Recognition
across most visitor scenarios remains unproven until those checks pass.

The [real-image quality audit](guided-capture-quality-audit-2026-11-07.md)
separates the observed incomplete/wrong-extent masks from a sharpness
preprocessing defect. The local sharpness correction preserves native pixels
for target crops below 256 pixels; it is not a replacement model or proof that
smooth targets can be judged automatically. This follow-up is not deployed.
