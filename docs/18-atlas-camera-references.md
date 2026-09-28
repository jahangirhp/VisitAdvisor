# Atlas-backed camera references

The camera server now loads canonical object/source metadata and prepared visual
references directly from Atlas. This supersedes the packaged-catalog requirement
in documents 16 and 17. Recognition remains server-side, candidate-only, with the
same DINO ranking, geometry gate (0.4), display floor (0.55), margin (0.04), and
three-frame stability policy. The phone receives no model or descriptors.

## Configuration and local startup

Install the existing server extra:

```bash
.venv/bin/pip install -e '.[dev,camera-server]'
```

Configure `MONGODB_URI` and `MONGODB_DATABASE` through the ignored `.env` file or
runtime environment. For the existing two-artwork selection, add:

```dotenv
LIVE_CAMERA_LOCATION_ID=6a9dd4106d52c3bc3965869d
LIVE_CAMERA_OBJECT_IDS=["6a9dd4126d52c3bc396586a0","6a9dd4136d52c3bc396586a1"]
LIVE_CAMERA_REFRESH_SECONDS=30
LIVE_CAMERA_BUNDLE=.local/live-camera
```

This retains the bounded trial: one active location, 2–12 explicit object IDs,
and 1–4 views per object. All selected objects must be ready; invalid selections
fail visibly rather than silently dropping objects. Changing the selected IDs
requires restarting with updated configuration, but no image rebuild. Updating
references for selected objects requires neither restart nor rebuild.

The model directory must contain `model.onnx` and `model-metadata.json`. The latter
contains only model identity, hash and preprocessing metadata, never object data.
Existing demo preparation already produced these files. For a fresh model build,
install the existing `visual,camera` preparation extras and cached DINO weights as
in document 07, then run:

```bash
.venv/bin/python -m app.modules.live_camera.export --model-only
.venv/bin/uvicorn app.modules.live_camera.server:app --port 8003 --no-access-log
```

The server reads neither `catalog.json` nor reference photos. Model-only export
requires no database access. Open `http://localhost:8003/`; physical-phone use
still requires trusted HTTPS. `GET /bundle/catalog.json` remains the public API
path for browser compatibility, but its response is generated from Atlas-backed
state. HTTP 200 from the HTML page alone does not establish recognition readiness;
verify the catalog endpoint and a frame request.

The normal application's `/live/` mount shares its existing async MongoDB client.
The standalone camera server owns one lazy async client and closes it on shutdown.
Neither camera path creates indexes or writes to Atlas. Use a read-only database
identity for the standalone deployment; its API still exposes no domain write routes.

## Memory and freshness

The first catalog/recognition request loads references and the ONNX model. Every
subsequent request reuses the process-local descriptors and model. No Redis,
background worker, new collection, or per-frame descriptor download is introduced.

On the first request after the refresh interval (default 30 seconds, configurable
5–120), the server checks selected objects, location status, source evidence,
reference-set IDs, model identity and preprocessing. It reads metadata and source
evidence; large descriptors are downloaded again only when that version changes.
The reference publication contract requires a new `set_id` whenever descriptors
change; unsupported direct edits of descriptors under the same ID are not detected.

Changes to public object/source metadata also change the response version. A new
set is fully validated, including local features and source membership, and
metadata is checked again before it replaces the old in-memory set. Refresh is
serialized per process; the ONNX session is reused across reference versions.

This is bounded eventual freshness, not an instantaneous database change stream:
a change can remain undetected until the next refresh interval. Requests wait for
a due refresh. A failed check clears the eligible cache and returns a sanitized
503, with a two-second retry backoff. Old descriptors are not used as an outage
fallback. The complete refresh has a 20-second deadline in addition to the existing
per-operation database timeout. Successful recovery reloads references using the
existing model session.

Location/version checks run before accepting a frame. Freshness is checked again
before inference and before returning results; a newly detected version change
returns 409 instead of a stale result. The browser clears tracking, reloads public
metadata, and requires a new Start tap after a version change. Stop/background
behavior and frame upload limits are unchanged.

`created_at` and seven-day `expires_at` remain in the public response for browser
compatibility. This browser metadata expiry does not replace the much shorter
server freshness interval. It no longer means an exported file needs redeployment.

## Data readiness and provenance

Atlas must contain current `visual_references` with:

- the exact model and reference preprocessing recorded in model metadata;
- source evidence hashes matching current canonical objects and sources;
- source-linked regional embeddings and valid local geometry descriptors.

Missing, incompatible, moved, deleted, or stale records prevent readiness. Prepare
references with the existing visual preparation CLI; the camera does not download
images or repair canonical data during a scan. No demo source or generated story
is promoted to canonical evidence automatically.

The read-only development check found compatible references for both trial objects,
with two views per object. The historical recorded demo has an extra full Milkmaid
reference, so changing to the canonical Atlas set is not pixel-for-pixel reference
parity with that demo. Its recorded accuracy results do not certify this reference
set. Re-run accuracy evaluation before making new accuracy claims or switching a
public trial. The algorithm and thresholds themselves are unchanged.

## Deployment

```bash
.venv/bin/python -m scripts.prepare_camera_server
```

Staging now includes the model, model metadata, camera application and required
PyMongo/settings dependencies. It excludes the catalog snapshot; a leftover
`bundle/catalog.json` from a previous staging run is removed. Supply the MongoDB
credentials securely at runtime and allow the deployment's outbound access in Atlas.
Never bake `.env` or connection strings into the image. Existing Cloud Run instances
are not changed by staging and must be deployed separately with the new settings.

The explicit `create_camera_app(bundle=...)` / file recognizer interface remains
for historical regression replay. Normal module startup never falls back to those
files if Atlas is unavailable. Frozen validation artifacts remain unchanged.

## Validation

Tests cover version refresh without reloading the model, descriptor reuse on
unchanged versions, source edits, moved/deleted objects, inactive locations,
incompatible models, invalid geometry/vectors/provenance, refresh races, sanitized
outages and recovery, client shutdown, public descriptor exclusion, and stale
inference rejection. Browser tests cover metadata reload without silently restarting
the camera. File and Atlas inputs produce identical candidate scores for the same
reference data.

A real, read-only Atlas check loaded two objects with geometry enabled in about
11 seconds; a subsequent process-local cache lookup took 0.034 ms. These are
single-run loading measurements, not frame latency, p95, or phone performance.

Final verification: 274 Python tests passed (one opt-in test skipped), eight
JavaScript unit tests passed, Ruff lint/format and mypy passed. Eighteen browser
checks passed initially; the two outdated unavailable-message assertions were
updated and passed in Chromium and WebKit on targeted rerun.
A real Atlas-backed API smoke returned 200 for catalog and recognition, rejected
a blank frame with zero scores for both objects, and returned 404 for model
downloads. That single blank-frame round trip was 132 ms on the development host.
The staged application imported successfully with no catalog or `.env` file.
Docker execution was not verified because the local Docker socket denied access.
No public deployment or Atlas writes were performed.
