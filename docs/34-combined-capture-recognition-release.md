# Combined capture and recognition release, 2026-11-08

Both feature branches are merged into `main` and published to GitHub:
`feat/guided-reference-capture` at `b9e9ada` and
`feat/object-photo-recognition` at `1d90dd3`. Merge commit `a2bed6e` contains the
combined application. Verification commit `8be6e41` updates documentation and an
older camera test expectation without changing runtime source.

The admin camera includes name/type and one **Save** action. Save uploads all
selected photos, embeds pending or changed references, and publishes the complete
set before reporting success. Existing-object photo uploads also embed on Save.
There is no separate Prepare action. Partial upload and embedding failures retain
the object and uploads; retries reconcile source hashes/session IDs and reuse an
unchanged published set. Original photos, source evidence and private masks remain
separate from derived descriptors.

The merged publisher preserves the object-focused preparation described in
[33](33-object-focused-admin-references.md). A changed preparation can replace a
view even when the total view count stays the same; an idempotent save must not
skip that replacement. A combined regression covers refreshing a legacy focused
view, retrying it without another encoding, and adding a new photo while retaining
the existing focused view. Visitor recognition, discovery, motion and performance
changes remain on their respective paths from documents 29–33.

## Validation

Required checks passed: 528 Python tests (two opt-in skips), Ruff lint and format,
`mypy app`, and 35 JavaScript tests. Browser coverage passed all 114 admin cases
(three opt-in skips) and all 96 visitor-camera cases across the configured browser
profiles. The full visitor run exposed two outdated 429 message assertions; the
corrected 429/503 recovery cases passed in both engines on targeted rerun.

Hosted preview checks covered 21 admin cases. One initial desktop WebKit
synthetic camera stream did not enable the shutter within the fixture deadline;
two isolated repetitions of the same four-photo Save case passed. The hosted
production Save checks passed in Chromium and the iPhone 12 WebKit profile.
These admin browser cases mock writes; backend tests use fake Atlas and encoders.
Validation created no production objects, uploads or reference sets.

Both preview and production passed real recognition loops in Chromium and WebKit
using the existing synthetic changed-background Circus image with simulated hand
movement. Each acquired Circus after three HTTP 200 responses without page errors.
This is a saved-image regression, not a new physical-phone or independent-photo
accuracy measurement.

## Production

Cloud Run revision `visitadvisor-api-main-a2bed6e0a9c7` serves 100% of public traffic
at <https://visitadvisor-api-t4ckjib5aq-ez.a.run.app>. Image digest:
`sha256:2f24ad69a7e27a9c5f4369606ee9d085654ff41711bcacc0063d7e5f16b608a0`.
Cloud Build: `6b22e6d0-bdf0-4a29-b233-7dff13a5ce33`.

The build layers the combined application source over the preceding immutable
production image, retaining its prepared models and dependencies. Runtime source
hashes match `main`; only six admin files differ from the preceding production
application. Health/database, seven public asset hashes and cache headers, the
protected Save route, runtime identity, environment, CPU/memory, and one minimum
instance were verified. The catalog retains all 115 objects and reference version
`247a6f5b008a8ea79f2feef8ee4226ab5ed9881d00c2d1ed2b31dfd7e0857664`.

Previous revision `visitadvisor-api-polish-da3d4cb98e3e` remains available for
traffic rollback. No catalog or reference migration was required.
