# Automatic camera reference discovery

The subsequent [3D matching correction](30-3d-reference-matching-correction.md)
updates the geometry policy described below.

The deployed camera discovers prepared references for the selected active venue,
including the default venue. The legacy two-object environment selection no longer
restricts the default visitor catalog. Explicit file-based historical replays and
direct fixed-selection loader callers retain their existing behavior.

Discovery checks the deployed model and preprocessing fingerprint, current object
location, source membership and canonical evidence hash. Stale, incompatible,
moved or missing-evidence records are excluded. Descriptor loading still pins each
immutable reference set and rechecks the metadata snapshot before publication.
The existing freshness lease and reference-version invalidation apply to additions
and removals. Database errors disable scanning instead of using stale references.

Legacy SIFT references with tied keypoint responses are capped to 800 paired
points and descriptors in memory, matching current preparation. Stored originals
and reference records are not rewritten.

The bounded venue catalog supports up to 500 reference records, matching the
existing visual catalog bound. The server recognizer and browser validate the same
limit. An oversized catalog returns an explicit error instead of truncating it.
Recognition thresholds, tentative-match presentation and source provenance are
unchanged. Saved photos still require successful reference preparation; preparation
creates image descriptors, not training of a new general-purpose model.

Admin routine status changes are announced to assistive technology without a
visible bottom toast. Upload progress and retry errors remain inside the object
editor; other errors appear inline above the collection or login form.

Regression checks cover discovery beyond the legacy object list and twelve-object
limit, newly prepared objects after refresh, shared model reuse, stale/incompatible
and moved records, missing sources, bounded catalogs, browser scanning with 113
objects, and desktop/WebKit/iPhone admin feedback. Mock camera fixtures verify
integration, not independent recognition accuracy.

Validation on 2026-11-08: 500 Python tests passed (two opt-in tests skipped),
Ruff lint/format and mypy passed, 33 JavaScript unit tests passed, 27 selected
admin browser checks and four visitor browser checks passed across Chromium,
WebKit and the iPhone 12 emulation profile where configured. The deployed preview
loaded the actual 113-object Rijksmuseum catalog, including Santa train. A saved
Santa photo replay ranked Santa first; a blank frame produced zero candidate
scores. This is a reference replay, not an independent photo accuracy measurement.
No production object, source or photo records were changed for these checks.

## Idle restart and busy retries

Production logs on 2026-11-08 showed a first recognition request taking 75.7
seconds while a new Cloud Run instance started; a queued retry returned HTTP 429.
The browser's eight-second request deadline expired before startup completed.
The existing service now retains one warm instance through the service-level
minimum, keeping its existing maximum, memory, CPU and runtime environment.
This retains idle capacity and therefore incurs idle-instance hosting charges.

HTTP 429 responses now respect a bounded Retry-After delay and continue scanning
without counting as connection failures. Stop, backgrounding and venue changes
still cancel retries. Genuine network/server failures retain their existing
bounded retry behavior. Frames are neither queued nor saved, and the recognition
algorithm and thresholds are unchanged.

After enabling the warm minimum, six consecutive read-only Santa reference
replays returned HTTP 200 in 2.17–2.75 seconds. Browser regressions cover more than
three busy responses, recovery through interleaved server errors, cancellation
during a busy wait and cancellation of an in-flight request in Chromium and
WebKit. These timings are warm server/reference replay measurements, not a
physical-phone or independent accuracy assessment.

The full catalog also made repeated list-to-array conversions significant for
WebKit's scan cadence. Reference points and descriptors are now converted once
per reference version; query points and descriptors are converted once per
frame. Every reference view is still geometrically checked with the same exact
matcher, scoring, floor and margin. The JSON-compatible source-linked catalog
remains available for historical replays. A one-CPU synthetic 141-view comparison
preserved candidate results and reduced scan time from 0.87–0.95 seconds to
0.65 seconds on the development host before the query conversion improvement.

The final preview's actual browser loop acquired Santa train after three HTTP
200 scans in Chromium and WebKit, without page errors. Server processing across
those six frames took 0.86–1.05 seconds, versus approximately 2.3 seconds before
numeric caching. Synthetic camera input replayed a saved reference photo; this
does not establish physical iPhone performance or independent recognition
accuracy. Both the full 113-object catalog and unchanged runtime environment,
CPU/memory limits and service-level warm minimum were verified.
