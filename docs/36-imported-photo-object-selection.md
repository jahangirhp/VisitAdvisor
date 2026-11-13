# Object selection for imported reference photos

The Add object gallery and native-camera imports now enter the same still-photo
selection flow as the admin shutter. Download an online photograph, choose it
with **Choose photos**, then inspect its object suggestion. No remote URL fetcher
or internet photo discovery is added.

The imported photo is decoded and bounded on the device, then encoded as a JPEG
with a maximum 1600-pixel edge and 1 MiB before analysis. Analysis uses the existing
authenticated same-origin endpoint, sequential queue, limits and pinned model.
Importing does not request camera access again. At most four photos are retained.

Tap a thumbnail to inspect the suggestion, draw a manual object area, or keep the
full photo. Automatic masks remain suggestions, not a claim of complete object
coverage. Flat-artwork and scene profiles preserve their existing skip behavior;
a manual area is still available. Analysis failure leaves the original available.

Saving sends the optimized source photograph and separate mask/selection metadata
through the existing upload and preparation flow. The existing object-focus-v1
preparer removes masked background onto white padding, or crops a selected area.
It preserves source hashes and derivative linkage. Rooms/buildings retain context.
A manual rectangle retains background inside that rectangle; it is not semantic
background removal. The original single-photo form for existing objects and
historical remotely fetched references are unchanged.

This extends acquisition tooling, not the encoder or recognition thresholds.
It does not establish independent visitor recognition accuracy. Existing online
references are not silently rewritten, and no production photo records are changed.

## Validation

128 focused Python tests passed, as did 35 JavaScript unit tests and Ruff lint
and format checks. Chrome passed the 22 existing quick-capture cases and the two
new imported-photo save cases, checking separate masks, explicit full-photo
fallback, exact source linkage and no additional camera permission request.
The Playwright browser download timed out; these checks used installed Chrome.
They mock analysis and Atlas, and do not measure segmentation or visitor accuracy.

The required full pytest command stops at four pre-existing Windows collection
errors because Unix resource is unavailable. Linux-platform mypy passed all 87
application source files. Native Windows checking has platform-specific typing
limitations in resource usage and multiprocessing connections.
