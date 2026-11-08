# Handheld motion filtering

The user's 2026-11-08 iPhone screenshot showed Santa clearly inside the camera
guide while Detection read "Move slowly, then pause on one object." That message
is emitted when the client rejects a response because its local motion check
finds the preview different from the uploaded frame. It does not establish the
server's winning candidate or score.

The old motion signature sampled one pixel at each of 256 grid locations.
Detailed packaging, reflective surfaces and small hand shifts could repeatedly
reset acquisition, even when all server responses supported the same object.
A six-pixel-shift textured-object regression failed to acquire before the fix,
both in the pure stability test and the Chromium camera loop.

The signature now averages each of the same 16-by-16 image regions. This changes
only the local motion representation; the uploaded image remains unchanged.
The existing movement limits, candidate score/margin thresholds, three agreeing
responses, response age limit, stalled-video checks and Stop behavior remain.
Paintings and 3D recognition algorithms, source data and prepared references are
not changed.

The corrected regression acquires the object during repeated small shifts in
Chromium and WebKit. A large displacement still resets acquisition. Tests also
cover movement after acquisition, retained last-seen cards, stalled video,
reference changes and stopping during a request. Saved private Santa frames were
used separately to compare motion measurements: for an eight-pixel translation,
one oblique view changed from 0.114 with point samples to 0.068 with block means,
below the unchanged acquisition movement limit of 0.09. These are controlled
motion diagnostics, not independent identity accuracy or physical-phone
acceptance measurements.

Development checks: 511 Python tests passed, two opt-in tests skipped; Ruff
lint/format and mypy passed; 35 JavaScript tests passed; ten selected Chromium
and WebKit browser checks passed. An additional 12 targeted browser checks
passed for bounded crops, switching/backgrounding, competing candidates, stalled
video recovery and handheld movement.

Deployment verification: revision visitadvisor-api-motion-d8aa2fab0f0d receives
100% of public traffic. The production JavaScript matches the checked source
and is served with Cache-Control: no-store. Environment, resource limits,
service account and one minimum instance were preserved.

On both preview and production, Chromium and WebKit displayed Santa train using
a saved Santa photo translated eight pixels while each real recognition request
was in flight. Preview acquired after three HTTP 200 responses in each engine;
production used three in Chromium and four in WebKit, with no page exceptions.
This verifies the integrated loop with simulated camera motion and a saved
reference image; it does not establish accuracy for a new physical iPhone view.

The live catalog gained Circus box during verification (114 objects rather than
113), changing its version to
834a72ee3e4f1e4de3d61c4816218e7a47b00fc79560758faf084ef5e8493ca0.
Every existing public reference, including Santa, was unchanged. No object or
photo records were modified by these checks.
