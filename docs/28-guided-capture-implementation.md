# Admin guided reference capture: implementation

Implemented on `feat/guided-reference-capture`, following the design in
[27](27-guided-reference-capture.md). Commit and publication were authorized on
2026-11-06. Publishing uses the existing VisitAdvisor service and preserves its
runtime configuration and recognition model assets. Implementation and automated
checks did not create production photo or catalog records.

## Admin flow

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

Preview and worker inference run independently. Analysis samples fresh decoded
frames with one request in flight, drops intermediate video, and waits at least
650 ms between completed analyses. It promises no fixed frame rate. Analysis
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
