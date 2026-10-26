# Camera-to-prepared-content integration — ticket #15

Development date: 2026-10-26. Branch: `MVP-15-camera-prepared-content`,
based on `MVP-8-mobile-visitor-ui` at `63d5cec`.

Implementation connects a stable **tentative** camera candidate to stored visitor
content. It does not enable automatic identification, restore manual picking,
generate content, add voice, or change recognition policy. #21 still owns factual
review and real launch-catalog readiness.

## Visitor behavior

The existing compact camera UI keeps its possible-match label, canonical object
summary and source links. On acquisition it loads introduction, facts, story,
history and interesting fact. Missing sections are explicit. Citation details
show the exact stored quotation and a safe source link where available. All text
uses text nodes, never generated HTML. Current prepared records are `unreviewed`;
the page labels them **AI-prepared content · Not yet fact-checked**. Citation
validation does not establish that every claim follows from its quotation.

Loading, missing preparation, outdated evidence and network/unavailable content
have distinct states. Scanning continues independently of the content request.
There is at most one active content request in addition to the existing one
in-flight recognition request. Content is fetched once per acquired candidate,
not on every frame; reacquisition fetches it again. It is not an offline cache or
a live subscription to content edits.

Clearing or switching a candidate, location/catalog reload, reference change,
Stop and backgrounding cancel the content request and invalidate its response.
A late response cannot restore hidden or replaced content, even when cancellation
cannot prevent the server from completing its read. Requests have a ten-second
client deadline; the server content read/recheck is bounded to eight seconds.

## Read-only camera API

```http
GET /objects/{object_id}/experience?location_id=ID&reference_version=VERSION
```

This path is relative to the camera host, so it is `/live/objects/.../experience`
when mounted by the main app. It works on the standalone read-only camera host
without exposing the main administrative, paid-provider or domain write routes.
The existing main API `/api/v1/objects/{id}/experience` remains unchanged.

The route accepts only objects in the current configured camera catalog, validates
location/version and reference freshness, and reads the same `prepared_content`
record used by the main experience API. The database query is location-scoped.
It validates the stored schema, object/catalog source membership, source location,
current canonical evidence hash and each citation's source ID/exact quotation.
Missing, moved, malformed, stale or unlinked records fail without returning
prepared text. Reference freshness/version is checked again after reading.
Reference freshness retains the existing bounded lease; it is not a transaction
or instantaneous detection of every concurrent source edit.

Responses contain only object/location/version, visitor sections, source IDs and
review status. Provider/model/token metadata, raw source records and connection
details are excluded. Reads are uncached (`Cache-Control: no-store`), use the
existing async PyMongo client, call no provider, and perform no writes. Historical
file-only replays have no canonical content fallback and return unavailable.
The staging script includes the new route, models and frontend module.

## Validation and remaining acceptance

Python tests exercise scoped content, private-field exclusion, read-only methods,
missing/moved objects, foreign sources/citations/quotes, invalid content, changed
source evidence, expired references, database outages and reference changes during
reading. Browser fixtures exercise ten identities at 320 CSS pixels, content/source
rendering, missing/sparse/outdated/unavailable content, target/location changes,
Stop/backgrounding, mismatched response identity/location/version/citations and
HTML-like text. Browser fixtures test integration, not recognition accuracy.

Recorded development checks: 349 Python tests passed, two opt-in tests skipped;
Ruff lint/format and `mypy app` passed; 15 JavaScript unit tests passed;
26 Chromium and 26 WebKit camera browser tests passed. Staged standalone import succeeded with
only staged source modules. WebKit initially could not launch because local
runtime libraries were missing. Existing Ubuntu packages `libnice10` and
`libgupnp-igd-1.6-0` were downloaded and extracted under the ignored local browser
dependency directory, without installing system packages. Reproduce with:

```bash
LD_LIBRARY_PATH="$PWD/.local/live-camera/browser-deps/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  npx playwright test tests/camera/browser/camera.spec.mjs --project=webkit
```

The ten-object test saves a narrow-screen screenshot in Playwright test output.

A live read-only smoke attempt returned `REFERENCES_UNAVAILABLE` before catalog
readiness. A separate database connectivity check failed; no successful live
content read, staging deployment, physical iPhone trial or ten-real-object flow
is claimed. No database changes or provider calls were performed. The application
still has one configured location; the browser location test exercises catalog
reload/lifecycle isolation, not a claim of multi-destination support.

Ticket #15 must remain open until the required supported-platform/staging and
real-content checks are complete. Passing fixtures does not close #1, #9 or #21.

A later read-only Atlas catalog query on 2026-10-26 succeeded: 128 Rijksmuseum
object records were present. The configured camera objects, The Milkmaid and
The Jewish Bride, have no prepared content. Three other objects have unreviewed
prepared content. This confirms a real launch-data gap for #21; it does not
establish a successful camera-host content smoke test.

The reported movement/card-disappearance bug is tracked in #31 under #7. This
integration preserves the existing tracking reset behavior; persistent last-seen
reading state is a separate follow-up, not a fix included in ticket #15.
